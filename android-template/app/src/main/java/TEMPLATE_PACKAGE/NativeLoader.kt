package @@PACKAGE_NAME@@

import android.content.res.AssetManager

internal object NativeLoader {
    init {
        System.loadLibrary("protected_loader")
    }

    external fun initialize(assets: AssetManager): Boolean
    external fun loadResource(path: String): ByteArray?
}
