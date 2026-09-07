import AppKit

// Пузырь с репликой над аватаром. Текст — в NSTextView (создан с полноценной
// текст-системой, поэтому корректно переносится по словам). Пузырь растёт по
// высоте под текст; если не влезает — скроллится (скроллбар скрыт, крутится
// колесом/трекпадом). Появляется на N секунд и гаснет.
final class SpeechBubble: NSView {

    private let pill = NSView()
    private let scroll = NSScrollView()
    private let textView = NSTextView(frame: NSRect(x: 0, y: 0, width: 200, height: 40))
    private var hideTimer: Timer?
    /// true → пилюля прижата к верху области и растёт вниз (для полноростовой
    /// голограммы); false → к низу, над головой компактного персонажа.
    var anchorTop = false
    private let font = NSFont.monospacedSystemFont(ofSize: 12.5, weight: .medium)
    private let hPad: CGFloat = 14
    private let vPad: CGFloat = 10

    override init(frame frameRect: NSRect) {
        super.init(frame: frameRect)
        wantsLayer = true

        pill.wantsLayer = true
        pill.layer?.backgroundColor = NSColor(srgbRed: 0.02, green: 0.09, blue: 0.18, alpha: 0.80).cgColor
        pill.layer?.cornerRadius = 10
        pill.layer?.masksToBounds = false
        pill.layer?.borderColor = NSColor(srgbRed: 0.34, green: 0.88, blue: 1, alpha: 0.55).cgColor
        pill.layer?.borderWidth = 1
        pill.layer?.shadowColor = NSColor(srgbRed: 0.34, green: 0.88, blue: 1, alpha: 1).cgColor
        pill.layer?.shadowOpacity = 0.55
        pill.layer?.shadowRadius = 12
        pill.layer?.shadowOffset = .zero
        addSubview(pill)

        scroll.drawsBackground = false
        scroll.hasVerticalScroller = false
        scroll.hasHorizontalScroller = false
        scroll.autohidesScrollers = true
        scroll.verticalScrollElasticity = .allowed
        pill.addSubview(scroll)

        textView.drawsBackground = false
        textView.isEditable = false
        textView.isSelectable = true
        textView.font = font
        textView.textColor = NSColor(srgbRed: 0.75, green: 0.95, blue: 1, alpha: 1)
        textView.alignment = .center
        textView.textContainerInset = NSSize(width: 2, height: 2)
        textView.isVerticallyResizable = true
        textView.isHorizontallyResizable = false
        textView.minSize = .zero
        textView.maxSize = NSSize(width: CGFloat.greatestFiniteMagnitude, height: CGFloat.greatestFiniteMagnitude)
        textView.textContainer?.widthTracksTextView = true
        textView.textContainer?.lineFragmentPadding = 2
        scroll.documentView = textView

        alphaValue = 0
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) не поддерживается") }

    func say(_ text: String, seconds: TimeInterval = 4) {
        hideTimer?.invalidate()

        let pw = max(150, bounds.width - 6)        // ширина пузыря почти во всё окно
        let innerW = pw - hPad * 2
        let textW = innerW - textView.textContainerInset.width * 2 - 4

        // Высоту меряем НЕЗАВИСИМЫМ layout-манагером с фиксированной шириной —
        // не полагаемся на ширину textView в скролле (она ещё не готова).
        let th = measuredHeight(text, width: textW) + textView.textContainerInset.height * 2 + 6

        let maxH = max(60, bounds.height - vPad * 2 - 2)
        let contentH = min(th, maxH)
        let ph = contentH + vPad * 2

        pill.frame = NSRect(x: (bounds.width - pw) / 2,
                            y: anchorTop ? bounds.height - ph : 0,
                            width: pw, height: ph)
        scroll.frame = NSRect(x: hPad, y: vPad, width: innerW, height: contentH)   // задаём ДО текста
        textView.frame = NSRect(x: 0, y: 0, width: innerW, height: max(th, contentH))
        textView.string = text                       // теперь ширина контейнера верная → перенос
        scroll.contentView.scroll(to: .zero)
        scroll.reflectScrolledClipView(scroll.contentView)

        NSAnimationContext.runAnimationGroup { $0.duration = 0.25; animator().alphaValue = 1 }
        hideTimer = Timer.scheduledTimer(withTimeInterval: seconds, repeats: false) { [weak self] _ in
            NSAnimationContext.runAnimationGroup { $0.duration = 0.4; self?.animator().alphaValue = 0 }
        }
    }

    private func measuredHeight(_ text: String, width: CGFloat) -> CGFloat {
        let storage = NSTextStorage(string: text, attributes: [.font: font])
        let container = NSTextContainer(size: NSSize(width: width, height: CGFloat.greatestFiniteMagnitude))
        container.lineFragmentPadding = 2
        let manager = NSLayoutManager()
        manager.addTextContainer(container)
        storage.addLayoutManager(manager)
        manager.ensureLayout(for: container)
        return ceil(manager.usedRect(for: container).height)
    }
}
