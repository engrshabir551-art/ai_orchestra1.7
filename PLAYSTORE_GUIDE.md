# From Streamlit app to a phone app

A Streamlit app is a website. The Play Store only accepts Android packages, so there are three routes.

## Route 1: install on your phone today (free, no Play Store)
1. Put the app on an always-on server with HTTPS (see "Hosting" below), or use your PC plus a private VPN such as Tailscale.
2. On the phone open the address in Chrome, tap the three dots, then **Add to Home screen**.
You get an icon that opens the app full screen. This is the quickest route and the safest for a money tool you use yourself.

## Route 2: Android wrapper app -> Play Store
1. Install Android Studio. New Project > **Empty Views Activity** (Kotlin). Name it AI Orchestra.
2. In `AndroidManifest.xml` add above `<application>`: `<uses-permission android:name="android.permission.INTERNET" />`
3. Replace `MainActivity.kt` (keep your package line) with:
```kotlin
import android.annotation.SuppressLint
import android.os.Bundle
import android.view.WindowManager
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.OnBackPressedCallback
import androidx.appcompat.app.AppCompatActivity

class MainActivity : AppCompatActivity() {
    @SuppressLint("SetJavaScriptEnabled")
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.setFlags(WindowManager.LayoutParams.FLAG_SECURE, WindowManager.LayoutParams.FLAG_SECURE) // blocks screenshots
        val web = WebView(this)
        setContentView(web)
        web.settings.javaScriptEnabled = true
        web.settings.domStorageEnabled = true
        web.webViewClient = WebViewClient()
        web.loadUrl("https://YOUR-DOMAIN-HERE")
        onBackPressedDispatcher.addCallback(this, object : OnBackPressedCallback(true) {
            override fun handleOnBackPressed() { if (web.canGoBack()) web.goBack() else finish() }
        })
    }
}
```
4. Test on your phone (USB or emulator). Then Build > Generate Signed App Bundle (.aab). Keep the signing key safe.
5. Play Console (one-time USD 25 developer fee): create the app, upload the .aab, add a privacy policy URL, complete the Data safety form and any financial-features declaration.
6. Use the **Internal testing** track first: you (and up to 100 testers) install from Play within minutes. Check Google's current rules for personal accounts, which may require a closed test with a minimum number of testers for some days before production release.

Be aware: Google can reject apps that are only a website in a frame, and apps that move money get extra policy review. A private tool for your own use is best kept on Route 1 or internal testing.

## Route 3: a true native app (best for a public product)
Move the logic in `core/` behind a FastAPI backend and build the screens in Flutter or Kotlin. Same ledger, agents and limits, but a real app with push notifications, biometric approval and faster screens. Ask for this as the next stage.

## Hosting (needed for routes 1 and 2)
1. Rent a small Linux server (any provider) and point a domain name at it.
2. Install Docker, copy this folder to the server, create a file `.env` containing `DOMAIN=your.domain.com`.
3. Run `docker compose up -d --build`. Caddy gets the HTTPS certificate automatically.
4. Open the address, create the admin account, then add your API key on Security and Settings.
Back up the `data` folder (database and saved keys). Use a strong password. The app locks a username after 5 failed sign-ins.

## If the app will move other people's money
Running a payments or money-transfer service for the public needs licences (in Australia AFSL/AUSTRAC registration as applicable) and a compliant provider. Get legal advice before launch. Using it for your own treasury is a different situation.
