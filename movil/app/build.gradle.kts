plugins {
    id("com.android.application")
}

android {
    namespace = "com.jarvis.movil"
    compileSdk = 37

    defaultConfig {
        applicationId = "com.jarvis.movil"
        minSdk = 26
        // 34: teclado y barras del sistema con el comportamiento clásico (sin «edge to edge» forzado).
        targetSdk = 34
        versionCode = 1
        versionName = "1.0"
    }
    buildTypes {
        release {
            isMinifyEnabled = false
        }
    }
    compileOptions {
        sourceCompatibility = JavaVersion.VERSION_17
        targetCompatibility = JavaVersion.VERSION_17
    }
    lint {
        abortOnError = false
        checkReleaseBuilds = false
    }
}
