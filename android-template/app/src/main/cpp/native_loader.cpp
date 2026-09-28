#include <jni.h>
#include <android/asset_manager.h>
#include <android/asset_manager_jni.h>

#include <algorithm>
#include <cstdint>
#include <limits>
#include <string>
#include <unordered_map>
#include <utility>
#include <vector>

#include "key_material.h"

#define APP_JNI_CLASS "@@JNI_CLASS@@"

namespace {
struct Entry {
    std::vector<uint8_t> nonce;
    std::vector<uint8_t> encrypted;
};

constexpr uint64_t kMaxPackageBytes = 512ULL * 1024ULL * 1024ULL;
constexpr uint64_t kMaxResourceBytes = 256ULL * 1024ULL * 1024ULL + 16ULL;
std::unordered_map<std::string, Entry> g_entries;
bool g_ready = false;

uint16_t ReadU16(const uint8_t* data) {
    return static_cast<uint16_t>((static_cast<uint16_t>(data[0]) << 8) | data[1]);
}

uint64_t ReadU64(const uint8_t* data) {
    uint64_t value = 0;
    for (int i = 0; i < 8; ++i) value = (value << 8) | data[i];
    return value;
}

bool ValidPath(const std::string& path) {
    if (path.empty() || path.front() == '/' || path.find('\\') != std::string::npos || path.find('\0') != std::string::npos) return false;
    size_t start = 0;
    while (start <= path.size()) {
        const size_t end = path.find('/', start);
        const size_t length = (end == std::string::npos ? path.size() : end) - start;
        const std::string part = path.substr(start, length);
        if (part.empty() || part == "." || part == "..") return false;
        if (end == std::string::npos) break;
        start = end + 1;
    }
    return true;
}

bool DecodePackage(AAssetManager* assets) {
    AAsset* asset = AAssetManager_open(assets, "app.dat", AASSET_MODE_BUFFER);
    if (asset == nullptr) return false;
    const off_t length = AAsset_getLength(asset);
    if (length < 8 || static_cast<uint64_t>(length) > kMaxPackageBytes) {
        AAsset_close(asset);
        return false;
    }
    std::vector<uint8_t> bytes(static_cast<size_t>(length));
    size_t read = 0;
    while (read < bytes.size()) {
        const size_t requested = std::min<size_t>(1024 * 1024, bytes.size() - read);
        const int amount = AAsset_read(asset, bytes.data() + read, requested);
        if (amount <= 0) {
            AAsset_close(asset);
            return false;
        }
        read += static_cast<size_t>(amount);
    }
    AAsset_close(asset);
    if (bytes[0] != 'W' || bytes[1] != 'P' || bytes[2] != 'K' || bytes[3] != '1') return false;
    const uint32_t count = (static_cast<uint32_t>(bytes[4]) << 24) | (static_cast<uint32_t>(bytes[5]) << 16) |
                           (static_cast<uint32_t>(bytes[6]) << 8) | static_cast<uint32_t>(bytes[7]);
    if (count == 0 || count > 5000) return false;
    size_t offset = 8;
    std::unordered_map<std::string, Entry> parsed;
    for (uint32_t i = 0; i < count; ++i) {
        if (bytes.size() - offset < 22) return false;
        const uint16_t path_length = ReadU16(bytes.data() + offset);
        const uint64_t encrypted_length = ReadU64(bytes.data() + offset + 2);
        offset += 10;
        if (path_length == 0 || path_length > 4096 || encrypted_length < 16 || encrypted_length > kMaxResourceBytes ||
            bytes.size() - offset < 12ULL + path_length || bytes.size() - offset - 12ULL - path_length < encrypted_length) return false;
        Entry entry;
        entry.nonce.assign(bytes.begin() + offset, bytes.begin() + offset + 12);
        offset += 12;
        std::string path(reinterpret_cast<const char*>(bytes.data() + offset), path_length);
        offset += path_length;
        if (!ValidPath(path) || parsed.find(path) != parsed.end()) return false;
        entry.encrypted.assign(bytes.begin() + offset, bytes.begin() + offset + static_cast<size_t>(encrypted_length));
        offset += static_cast<size_t>(encrypted_length);
        parsed.emplace(std::move(path), std::move(entry));
    }
    if (offset != bytes.size()) return false;
    g_entries.swap(parsed);
    g_ready = true;
    return true;
}

bool HasException(JNIEnv* env) {
    if (!env->ExceptionCheck()) return false;
    env->ExceptionClear();
    return true;
}

jbyteArray ToJavaBytes(JNIEnv* env, const std::vector<uint8_t>& input) {
    if (input.size() > static_cast<size_t>(std::numeric_limits<jsize>::max())) return nullptr;
    jbyteArray result = env->NewByteArray(static_cast<jsize>(input.size()));
    if (result != nullptr && !input.empty()) env->SetByteArrayRegion(result, 0, static_cast<jsize>(input.size()), reinterpret_cast<const jbyte*>(input.data()));
    return result;
}

std::string ToUtf8(JNIEnv* env, jstring input) {
    jclass string_class = env->FindClass("java/lang/String");
    if (string_class == nullptr || HasException(env)) return {};
    jmethodID get_bytes = env->GetMethodID(string_class, "getBytes", "(Ljava/lang/String;)[B");
    jstring charset = env->NewStringUTF("UTF-8");
    auto bytes = static_cast<jbyteArray>(env->CallObjectMethod(input, get_bytes, charset));
    env->DeleteLocalRef(charset);
    env->DeleteLocalRef(string_class);
    if (HasException(env) || bytes == nullptr) return {};
    const jsize length = env->GetArrayLength(bytes);
    std::string result(static_cast<size_t>(length), '\0');
    if (length > 0) env->GetByteArrayRegion(bytes, 0, length, reinterpret_cast<jbyte*>(&result[0]));
    env->DeleteLocalRef(bytes);
    if (HasException(env)) return {};
    return result;
}

jbyteArray Decrypt(JNIEnv* env, const std::string& path, const Entry& entry) {
    jclass cipher_class = env->FindClass("javax/crypto/Cipher");
    jclass key_class = env->FindClass("javax/crypto/spec/SecretKeySpec");
    jclass params_class = env->FindClass("javax/crypto/spec/GCMParameterSpec");
    if (cipher_class == nullptr || key_class == nullptr || params_class == nullptr || HasException(env)) return nullptr;
    jmethodID get_instance = env->GetStaticMethodID(cipher_class, "getInstance", "(Ljava/lang/String;)Ljavax/crypto/Cipher;");
    jmethodID key_ctor = env->GetMethodID(key_class, "<init>", "([BLjava/lang/String;)V");
    jmethodID params_ctor = env->GetMethodID(params_class, "<init>", "(I[B)V");
    jmethodID init = env->GetMethodID(cipher_class, "init", "(ILjava/security/Key;Ljava/security/spec/AlgorithmParameterSpec;)V");
    jmethodID update_aad = env->GetMethodID(cipher_class, "updateAAD", "([B)V");
    jmethodID do_final = env->GetMethodID(cipher_class, "doFinal", "([B)[B");
    jstring transformation = env->NewStringUTF("AES/GCM/NoPadding");
    jobject cipher = env->CallStaticObjectMethod(cipher_class, get_instance, transformation);
    env->DeleteLocalRef(transformation);
    if (HasException(env) || cipher == nullptr) return nullptr;
    jbyteArray key = ToJavaBytes(env, std::vector<uint8_t>(kPackageKey, kPackageKey + sizeof(kPackageKey)));
    jstring algorithm = env->NewStringUTF("AES");
    jobject secret_key = env->NewObject(key_class, key_ctor, key, algorithm);
    env->DeleteLocalRef(key);
    env->DeleteLocalRef(algorithm);
    jbyteArray nonce = ToJavaBytes(env, entry.nonce);
    jobject params = env->NewObject(params_class, params_ctor, 128, nonce);
    env->DeleteLocalRef(nonce);
    env->CallVoidMethod(cipher, init, 2, secret_key, params);
    env->DeleteLocalRef(secret_key);
    env->DeleteLocalRef(params);
    if (HasException(env)) return nullptr;
    const auto aad_bytes = std::vector<uint8_t>(path.begin(), path.end());
    jbyteArray aad = ToJavaBytes(env, aad_bytes);
    env->CallVoidMethod(cipher, update_aad, aad);
    env->DeleteLocalRef(aad);
    if (HasException(env)) return nullptr;
    jbyteArray encrypted = ToJavaBytes(env, entry.encrypted);
    auto plain = static_cast<jbyteArray>(env->CallObjectMethod(cipher, do_final, encrypted));
    env->DeleteLocalRef(encrypted);
    if (HasException(env) || plain == nullptr) return nullptr;
    return plain;
}

jboolean NativeInitialize(JNIEnv* env, jobject, jobject java_assets) {
    AAssetManager* assets = AAssetManager_fromJava(env, java_assets);
    return assets != nullptr && DecodePackage(assets) ? JNI_TRUE : JNI_FALSE;
}

jbyteArray NativeLoadResource(JNIEnv* env, jobject, jstring java_path) {
    if (!g_ready || java_path == nullptr) return nullptr;
    const std::string path = ToUtf8(env, java_path);
    if (!ValidPath(path)) return nullptr;
    const auto found = g_entries.find(path);
    if (found == g_entries.end()) return nullptr;
    return Decrypt(env, path, found->second);
}
}  // namespace

JNIEXPORT jint JNICALL JNI_OnLoad(JavaVM* vm, void*) {
    JNIEnv* env = nullptr;
    if (vm->GetEnv(reinterpret_cast<void**>(&env), JNI_VERSION_1_6) != JNI_OK) return JNI_ERR;
    jclass loader = env->FindClass(APP_JNI_CLASS);
    if (loader == nullptr || HasException(env)) return JNI_ERR;
    JNINativeMethod methods[] = {
        {const_cast<char*>("initialize"), const_cast<char*>("(Landroid/content/res/AssetManager;)Z"), reinterpret_cast<void*>(NativeInitialize)},
        {const_cast<char*>("loadResource"), const_cast<char*>("(Ljava/lang/String;)[B"), reinterpret_cast<void*>(NativeLoadResource)},
    };
    const jint result = env->RegisterNatives(loader, methods, sizeof(methods) / sizeof(methods[0]));
    env->DeleteLocalRef(loader);
    return result == JNI_OK ? JNI_VERSION_1_6 : JNI_ERR;
}
