import AppKit

// Обычное окно со всей историей переписки. Открывается из меню-бара, при каждом
// открытии перечитывает лог и прокручивается к последним сообщениям.
final class HistoryWindowController {

    private var window: NSWindow?
    private var textView: NSTextView?
    private let log: ConversationLog
    private let name: String

    init(log: ConversationLog, name: String) {
        self.log = log
        self.name = name
    }

    func show() {
        if window == nil { build() }
        reload()
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    private func build() {
        let win = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 440, height: 540),
            styleMask: [.titled, .closable, .miniaturizable, .resizable],
            backing: .buffered, defer: false)
        win.title = "История диалога — \(name)"
        win.isReleasedWhenClosed = false
        win.center()

        let scroll = NSScrollView(frame: win.contentView!.bounds)
        scroll.autoresizingMask = [.width, .height]
        scroll.hasVerticalScroller = true
        scroll.drawsBackground = false

        let tv = NSTextView(frame: scroll.bounds)
        tv.isEditable = false
        tv.isSelectable = true
        tv.autoresizingMask = [.width]
        tv.textContainerInset = NSSize(width: 16, height: 16)
        tv.isVerticallyResizable = true
        tv.textContainer?.widthTracksTextView = true
        scroll.documentView = tv

        win.contentView?.addSubview(scroll)
        window = win
        textView = tv
    }

    private func reload() {
        guard let tv = textView else { return }
        let out = NSMutableAttributedString()
        let df = DateFormatter()
        df.dateFormat = "d MMM, HH:mm"
        df.locale = Locale(identifier: "ru_RU")

        if log.entries.isEmpty {
            out.append(NSAttributedString(
                string: "Пока пусто — начни переписку, наведясь на аватара.",
                attributes: [.font: NSFont.systemFont(ofSize: 13),
                             .foregroundColor: NSColor.secondaryLabelColor]))
        }

        for e in log.entries {
            if e.sender == "action" {
                let italic = NSFontManager.shared.convert(NSFont.systemFont(ofSize: 11),
                                                          toHaveTrait: .italicFontMask)
                out.append(NSAttributedString(
                    string: "⚙️ \(e.text) · \(df.string(from: e.ts))\n\n",
                    attributes: [.font: italic,
                                 .foregroundColor: NSColor.tertiaryLabelColor]))
                continue
            }
            let who = e.sender == "you" ? "Ты" : name
            out.append(NSAttributedString(
                string: "\(who) · \(df.string(from: e.ts))\n",
                attributes: [.font: NSFont.boldSystemFont(ofSize: 11),
                             .foregroundColor: NSColor.secondaryLabelColor]))
            out.append(NSAttributedString(
                string: e.text + "\n\n",
                attributes: [.font: NSFont.systemFont(ofSize: 13),
                             .foregroundColor: NSColor.labelColor]))
        }
        tv.textStorage?.setAttributedString(out)
        tv.scrollToEndOfDocument(nil)     // к последним сообщениям
    }
}
