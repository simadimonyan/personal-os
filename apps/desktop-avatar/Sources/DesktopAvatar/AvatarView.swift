import AppKit
import AVFoundation

// Содержимое окна аватара: живой персонаж (CreatureView), пузырь сверху, поле
// переписки снизу (всплывает при наведении). Окно небольшое, перетаскивается за
// тело. Сквозной режим прозрачных углов — снаружи (PassthroughController),
// поэтому здесь только геометрия и подсказки, какая точка «интерактивная».
final class AvatarView: NSView {

    private var clipsDir: URL
    private var player: AVQueuePlayer?
    private var playerLayer: AVPlayerLayer?
    private var looper: AVPlayerLooper?
    private var onFinish: (() -> Void)?

    private let creature: CreatureView
    private let lottie = LottieAvatarView(frame: .zero)
    private let holo = HologramView(frame: .zero)
    private var appearanceID = "blob"
    let bubble = SpeechBubble()
    let chatInput = ChatInput()

    init(frame: NSRect, clipsDir: URL, tint: NSColor, appearance: String = "blob") {
        self.clipsDir = clipsDir
        self.creature = CreatureView(frame: .zero, tint: tint,
                                     appearance: CreatureView.Appearance(rawValue: appearance) ?? .blob)
        super.init(frame: frame)

        wantsLayer = true
        layer?.backgroundColor = .clear

        addSubview(creature)
        lottie.isHidden = true
        addSubview(lottie)
        holo.isHidden = true
        addSubview(holo)
        addSubview(bubble)          // пузырь и поле — поверх всех обликов
        chatInput.isHidden = true
        chatInput.alphaValue = 0
        addSubview(chatInput)
        // Начальный облик (в т.ч. lottie:* / holo) применяем полноценно.
        setAppearance(appearance)
        needsLayout = true
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) не поддерживается") }

    /// Применить новый характер: цвет персонажа и папку клипов.
    func apply(tint: NSColor, clipsDir: URL) {
        creature.setTint(tint)
        self.clipsDir = clipsDir
    }

    /// Сообщить обликам id пресета характера (голограмма меняет репертуар поз).
    func setCharacter(_ preset: String) {
        holo.setCharacter(preset)
    }

    /// Сменить облик на лету. id: "blob" | "pet" | "lottie:{name}" | "holo".
    func setAppearance(_ id: String) {
        appearanceID = id
        playerLayer?.isHidden = true          // мог остаться видео-клип — прячем
        creature.isHidden = true
        lottie.stop(); lottie.isHidden = true
        holo.isHidden = true

        bubble.anchorTop = (id == "holo")
        if id == "holo" {
            holo.isHidden = false
            holo.frame = figureRect
            holo.setExpression("idle")
        } else if id.hasPrefix("lottie:") {
            let name = String(id.dropFirst("lottie:".count))
            lottie.isHidden = false
            lottie.frame = figureRect
            lottie.load(name: name, in: CharacterConfig.resourcesDirectory)
        } else {
            creature.isHidden = false
            creature.setAppearance(CreatureView.Appearance(rawValue: id) ?? .blob)
        }
        needsLayout = true
    }

    // MARK: - Геометрия

    /// Область фигуры. Голограмма — во весь рост (высокий кадр почти на всё окно);
    /// прочие облики — компактный квадрат.
    private var figureRect: CGRect {
        if appearanceID == "holo" {
            let w = min(bounds.width - 12, 260)
            return CGRect(x: (bounds.width - w) / 2, y: 4, width: w, height: bounds.height - 8)
        }
        let d = min(bounds.width - 40, 150)
        return CGRect(x: (bounds.width - d) / 2, y: 62, width: d, height: d)
    }

    override func layout() {
        super.layout()
        let r = figureRect
        creature.frame = r
        lottie.frame = r
        holo.frame = r
        playerLayer?.frame = r
        if appearanceID == "holo" {
            // пузырь — почти всё окно (до поля ввода): пилюля прижата к верху
            // (anchorTop) и растёт вниз ровно под объём текста
            bubble.frame = NSRect(x: 6, y: 52, width: bounds.width - 12, height: bounds.height - 58)
        } else {
            bubble.frame = NSRect(x: 6, y: r.maxY + 10,
                                  width: bounds.width - 12, height: bounds.height - r.maxY - 16)
        }
        chatInput.frame = NSRect(x: 16, y: 12, width: bounds.width - 32, height: 32)
    }

    // MARK: - Зоны интерактивности (для сквозного режима)

    /// Единая зона аватара: тело персонажа + поле ввода (и запас вокруг). Пока
    /// курсор здесь — окно ловит мышь и поле показано; ушёл — окно сквозное.
    private var hoverZone: CGRect {
        figureRect.union(chatInput.frame).insetBy(dx: -14, dy: -14)
    }

    /// Точка (в координатах окна) над интерактивной частью аватара.
    func overInteractive(windowPoint: NSPoint) -> Bool {
        let p = convert(windowPoint, from: nil)
        if hoverZone.contains(p) { return true }
        if bubble.alphaValue > 0.01 && bubble.frame.contains(p) { return true }
        return false
    }

    /// Показать/спрятать поле ввода (зовёт PassthroughController по наведению).
    func setHover(_ on: Bool) {
        if on && !chatInput.isHidden && chatInput.alphaValue > 0.9 { return }
        if !on && chatInput.isHidden { return }
        if on {
            chatInput.isHidden = false
        } else {
            chatInput.endEditing()          // курсор ушёл — снимаем фокус, иначе залипает
        }
        NSAnimationContext.runAnimationGroup({ ctx in
            ctx.duration = 0.18
            chatInput.animator().alphaValue = on ? 1 : 0
        }, completionHandler: { [weak self] in
            if !on { self?.chatInput.isHidden = true }
        })
    }

    // MARK: - Эмоции / воспроизведение

    func play(clip name: String, loop: Bool, onFinish: (() -> Void)? = nil) {
        // Голограмма клипов не воспроизводит — переводим имя клипа в выражение.
        if appearanceID == "holo" {
            let n = name.lowercased()
            if loop { holo.setExpression("idle"); onFinish?(); return }
            if n.contains("blink") { onFinish?(); return }        // моргание — внутри самой голограммы
            let expr: String = (n.contains("happy") || n.contains("wave")) ? "happy"
                : (n.contains("think") || n.contains("surprise") || n.contains("curious")) ? "curious"
                : "idle"
            holo.setExpression(expr)
            holo.pulse()
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.2) { [weak self] in
                self?.holo.setExpression("idle")
                onFinish?()
            }
            return
        }
        let url = clipsDir.appendingPathComponent(name)
        guard FileManager.default.fileExists(atPath: url.path) else {
            if !loop { creature.react() }
            onFinish?()
            return
        }
        ensurePlayer()
        creature.isHidden = true
        playerLayer?.isHidden = false
        self.onFinish = onFinish
        let item = AVPlayerItem(url: url)
        if loop {
            looper = AVPlayerLooper(player: player!, templateItem: item)
        } else {
            looper = nil
            player?.removeAllItems()
            player?.insert(item, after: nil)
        }
        player?.play()
    }

    private func ensurePlayer() {
        guard player == nil else { return }
        let p = AVQueuePlayer()
        let l = AVPlayerLayer(player: p)
        l.videoGravity = .resizeAspectFill
        l.backgroundColor = .clear
        l.frame = figureRect
        layer?.addSublayer(l)
        player = p
        playerLayer = l
        NotificationCenter.default.addObserver(
            self, selector: #selector(itemDidFinish),
            name: .AVPlayerItemDidPlayToEndTime, object: p.currentItem)
    }

    @objc private func itemDidFinish() {
        let cb = onFinish
        onFinish = nil
        cb?()
    }
}
