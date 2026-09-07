// swift-tools-version:5.9
import PackageDescription

// Каркас MVP. На Этапе 1 приложение запускается как обычный SwiftPM-executable
// (accessory-app без Dock-иконки). Упаковка в полноценный .app bundle — шаг
// финализации Этапа 1 (Info.plist, подпись, LSUIElement=1).
let package = Package(
    name: "DesktopAvatar",
    platforms: [.macOS(.v13)],
    dependencies: [
        // Airbnb Lottie — рендер After Effects / bodymovin JSON-анимаций
        // (векторные, с настоящим альфа-каналом) нативно в NSView.
        .package(url: "https://github.com/airbnb/lottie-spm.git", from: "4.4.0")
    ],
    targets: [
        .executableTarget(
            name: "DesktopAvatar",
            dependencies: [
                .product(name: "Lottie", package: "lottie-spm")
            ],
            path: "Sources/DesktopAvatar"
        )
    ]
)
