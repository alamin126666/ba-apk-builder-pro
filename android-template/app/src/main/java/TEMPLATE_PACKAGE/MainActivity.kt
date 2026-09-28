package @@PACKAGE_NAME@@

import android.app.Activity
import android.os.Bundle
import android.view.Gravity
import android.widget.TextView

class MainActivity : Activity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (!NativeLoader.initialize(assets)) {
            val error = TextView(this).apply {
                text = "This app could not load its protected web content. Please reinstall it."
                gravity = Gravity.CENTER
                setPadding(32, 32, 32, 32)
            }
            setContentView(error)
            return
        }
        setContentView(WebViewManager.create(this))
    }
}
