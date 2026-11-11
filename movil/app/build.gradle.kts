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
        // compilar.ps1 pasa una versión que sube en cada compilación (minutos desde 2026);
        // la app se actualiza sola cuando la PC tiene una mayor que la instalada.
        versionCode = (project.findProperty("jarvisVersionCode") as String?)?.toInt() ?: 1
        versionName = (project.findProperty("jarvisVersionName") as String?) ?: "1.0"
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
