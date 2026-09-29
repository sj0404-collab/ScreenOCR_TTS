plugins {
    id("com.android.application")
    id("org.jetbrains.kotlin.android")
}

android {
    namespace = "com.screenocr.client"
    compileSdk = 34

    defaultConfig {
        applicationId = "com.screenocr.client"
        minSdk = 23
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
    kotlinOptions {
        jvmTarget = "17"
    }
}

// Словари лежат в корне репозитория и в git не дублируются: на этапе сборки
// копируются в assets. Путь к репозиторию — родитель android/.
val repoRootDir = rootProject.projectDir.parentFile
val dictAssetsDir = layout.buildDirectory.dir("generated/dictAssets")

val syncDictAssets by tasks.registering(Copy::class) {
    description = "Копирует словари из корня репозитория в assets приложения"
    from(repoRootDir) {
        include("en_rus_full.json", "cities_dict.json", "game_names.json")
    }
    into(dictAssetsDir)
}

android.sourceSets.getByName("main").assets.srcDir(dictAssetsDir)

// Имя задачи syncDictAssets само заканчивается на Assets, поэтому отбираем
// только задачи сборки ресурсов AGP — иначе получается циклическая зависимость.
tasks.matching { it.name.startsWith("merge") && it.name.endsWith("Assets") }.configureEach {
    dependsOn(syncDictAssets)
}

tasks.withType<Test>().configureEach {
    systemProperty("screenocr.repoRoot", repoRootDir.absolutePath)
}

dependencies {
    implementation("androidx.core:core-ktx:1.12.0")
    implementation("androidx.appcompat:appcompat:1.6.1")
    implementation("androidx.activity:activity-ktx:1.8.2")
    implementation("com.google.android.material:material:1.11.0")

    testImplementation("junit:junit:4.13.2")
}