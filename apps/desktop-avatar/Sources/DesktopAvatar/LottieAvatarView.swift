import AppKit
import Lottie

// Облик-анимация: проигрывает скачанную Lottie-анимацию (векторный After
// Effects / bodymovin JSON с настоящим альфа-каналом) зацикленно и прозрачно.
// Файлы лежат в Resources/lottie/*.json. Персонаж рисуется не кодом, а готовой
// анимацией от дизайнера — чётко масштабируется, без фотореалистичной криповости.
final class LottieAvatarView: NSView {

    private let animView = LottieAnimationView()
    private var currentFile: String?

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true
        layer?.backgroundColor = .clear
        animView.contentMode = .scaleAspectFit
        animView.loopMode = .loop
        animView.backgroundBehavior = .pauseAndRestore   // не жрёт CPU когда скрыт
        addSubview(animView)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) не поддерживается") }

    override func layout() {
        super.layout()
        animView.frame = bounds
    }

    /// Загрузить и запустить анимацию из файла Resources/lottie/{name}.json.
    func load(name: String, in resources: URL) {
        guard currentFile != name else { play(); return }
        currentFile = name
        let path = resources.appendingPathComponent("lottie")
                            .appendingPathComponent("\(name).json").path
        if let anim = LottieAnimation.filepath(path) {
            animView.animation = anim
            animView.play()
        } else {
            FileHandle.standardError.write(
                Data("[avatar] lottie '\(name)' не найден по пути \(path)\n".utf8))
        }
    }

    func play() { animView.play() }
    func stop() { animView.pause() }
}
