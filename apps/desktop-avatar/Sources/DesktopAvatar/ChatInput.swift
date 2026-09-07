import AppKit

// Текстовое поле, которое при клике активирует приложение и делает окно key —
// иначе в borderless-окне accessory-приложения ввод не начинается.
private final class FocusField: NSTextField {
    override func mouseDown(with event: NSEvent) {
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
        super.mouseDown(with: event)
    }
}

// Поле переписки — аккуратная «пилюля»: контейнер с фоном и скруглением, внутри
// текстовое поле с отступами и вертикальным центрированием (без кривого бордера
// самого NSTextField). Enter отправляет и очищает.
final class ChatInput: NSView, NSTextFieldDelegate {

    var onSubmit: ((String) -> Void)?
    private let field = FocusField()

    /// Идёт ли ввод прямо сейчас (чтобы не прятать поле, пока печатают).
    var isEditing: Bool { field.currentEditor() != nil }

    /// Снять фокус (когда курсор ушёл — иначе поле «залипает» первым респондером).
    func endEditing() {
        if field.currentEditor() != nil { window?.makeFirstResponder(nil) }
    }

    init() {
        super.init(frame: .zero)
        wantsLayer = true
        layer?.backgroundColor = NSColor(srgbRed: 0.02, green: 0.09, blue: 0.18, alpha: 0.80).cgColor
        layer?.borderColor = NSColor(srgbRed: 0.34, green: 0.88, blue: 1, alpha: 0.55).cgColor
        layer?.borderWidth = 1
        layer?.shadowColor = NSColor(srgbRed: 0.34, green: 0.88, blue: 1, alpha: 1).cgColor
        layer?.shadowOpacity = 0.45
        layer?.shadowRadius = 10
        layer?.shadowOffset = .zero

        let mono = NSFont.monospacedSystemFont(ofSize: 12, weight: .regular)
        field.delegate = self
        field.placeholderAttributedString = NSAttributedString(
            string: "напиши мне…",
            attributes: [.foregroundColor: NSColor(srgbRed: 0.34, green: 0.62, blue: 0.82, alpha: 1),
                         .font: mono])
        field.font = mono
        field.textColor = NSColor(srgbRed: 0.75, green: 0.95, blue: 1, alpha: 1)
        field.isBordered = false
        field.isBezeled = false
        field.drawsBackground = false
        field.focusRingType = .none
        field.usesSingleLineMode = true
        field.lineBreakMode = .byTruncatingTail
        field.cell?.wraps = false
        field.cell?.isScrollable = true
        addSubview(field)
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) не поддерживается") }

    override func layout() {
        super.layout()
        layer?.cornerRadius = bounds.height / 2          // всегда идеальная пилюля
        let h = field.intrinsicContentSize.height
        field.frame = NSRect(x: 14, y: (bounds.height - h) / 2, width: bounds.width - 28, height: h)
    }

    // Клик по пилюле — фокус в поле (контейнер сам не редактируется).
    override func mouseDown(with event: NSEvent) {
        window?.makeFirstResponder(field)
    }

    func control(_ control: NSControl, textView: NSTextView, doCommandBy sel: Selector) -> Bool {
        guard sel == #selector(NSResponder.insertNewline(_:)) else { return false }
        let text = field.stringValue.trimmingCharacters(in: .whitespacesAndNewlines)
        if !text.isEmpty {
            onSubmit?(text)
            field.stringValue = ""
        }
        return true
    }
}
