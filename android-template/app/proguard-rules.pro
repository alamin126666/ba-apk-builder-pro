# JNI uses RegisterNatives and relies on these exact method names.
-keep class @@PACKAGE_NAME@@.NativeLoader { *; }
-keepclasseswithmembernames,includedescriptorclasses class * {
    native <methods>;
}
-keep class @@PACKAGE_NAME@@.MainActivity { *; }
-keepattributes *Annotation*
