import java.util.Properties

plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

// Signing keys never enter the repository: see scripts/build-android.sh.
val signing = Properties().apply {
    val file = System.getenv("PIGUARD_KEYSTORE_PROPERTIES")?.let(::File)
    if (file != null && file.exists()) file.inputStream().use(::load)
}

android {
    namespace = "de.lytex"
    compileSdk = 35

    defaultConfig {
        applicationId = "de.lytex"
        minSdk = 29  // the WebView shows certificates to the app from Android 10 on
        targetSdk = 35
        versionCode = (System.getenv("PIGUARD_VERSION_CODE") ?: "1").toInt()
        versionName = System.getenv("PIGUARD_VERSION_NAME") ?: "0.1.0"
    }

    signingConfigs {
        if (signing.isNotEmpty()) {
            create("release") {
                storeFile = file(signing.getProperty("storeFile"))
                storePassword = signing.getProperty("storePassword")
                keyAlias = signing.getProperty("keyAlias")
                keyPassword = signing.getProperty("keyPassword")
            }
        }
    }

    buildTypes {
        release {
            isMinifyEnabled = false
            if (signing.isNotEmpty()) signingConfig = signingConfigs.getByName("release")
        }
    }

    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    kotlinOptions { jvmTarget = "17" }
    sourceSets["main"].jniLibs.srcDir("build/rust-jni") // from scripts/build-android.sh
}

dependencies {
    implementation("androidx.appcompat:appcompat:1.7.0")
    implementation("androidx.biometric:biometric:1.1.0")
    implementation("com.google.android.material:material:1.12.0")
    implementation("com.squareup.okhttp3:okhttp:4.12.0")
    implementation("com.wireguard.android:tunnel:1.0.20230706")
}
