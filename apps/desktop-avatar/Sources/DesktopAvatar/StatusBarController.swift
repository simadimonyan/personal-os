import AppKit

// Иконка в статус-баре macOS с меню: скрыть/показать аватара и выключить.
final class StatusBarController: NSObject {

    private let statusItem: NSStatusItem
    private weak var window: NSWindow?
    private let toggleItem: NSMenuItem
    private let onShowHistory: () -> Void
    private let onSelectCharacter: (String) -> Void
    private let onSelectAppearance: (String) -> Void
    private let onToggle: (Bool) -> Void
    private var characterItems: [NSMenuItem] = []
    private var appearanceItems: [NSMenuItem] = []

    init(window: NSWindow,
         characters: [(id: String, title: String)],
         current: String,
         appearances: [(id: String, title: String)],
         currentAppearance: String,
         onShowHistory: @escaping () -> Void,
         onSelectCharacter: @escaping (String) -> Void,
         onSelectAppearance: @escaping (String) -> Void,
         onToggle: @escaping (Bool) -> Void) {
        self.window = window
        self.onShowHistory = onShowHistory
        self.onSelectCharacter = onSelectCharacter
        self.onSelectAppearance = onSelectAppearance
        self.onToggle = onToggle
        statusItem = NSStatusBar.system.statusItem(withLength: NSStatusItem.variableLength)
        toggleItem = NSMenuItem(title: "Скрыть", action: #selector(toggleWindow), keyEquivalent: "h")
        super.init()

        statusItem.button?.title = "🙂"

        let menu = NSMenu()

        // Подменю «Характер».
        let characterRoot = NSMenuItem(title: "Характер", action: nil, keyEquivalent: "")
        let characterMenu = NSMenu()
        for c in characters {
            let item = NSMenuItem(title: c.title, action: #selector(pickCharacter(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = c.id
            item.state = (c.id == current) ? .on : .off
            characterMenu.addItem(item)
            characterItems.append(item)
        }
        characterRoot.submenu = characterMenu
        menu.addItem(characterRoot)

        // Подменю «Облик».
        let appearanceRoot = NSMenuItem(title: "Облик", action: nil, keyEquivalent: "")
        let appearanceMenu = NSMenu()
        for a in appearances {
            let item = NSMenuItem(title: a.title, action: #selector(pickAppearance(_:)), keyEquivalent: "")
            item.target = self
            item.representedObject = a.id
            item.state = (a.id == currentAppearance) ? .on : .off
            appearanceMenu.addItem(item)
            appearanceItems.append(item)
        }
        appearanceRoot.submenu = appearanceMenu
        menu.addItem(appearanceRoot)

        let history = NSMenuItem(title: "История диалога…", action: #selector(showHistory), keyEquivalent: "")
        history.target = self
        menu.addItem(history)

        menu.addItem(.separator())
        toggleItem.target = self
        menu.addItem(toggleItem)

        menu.addItem(.separator())
        let quit = NSMenuItem(title: "Выключить", action: #selector(quit), keyEquivalent: "q")
        quit.target = self
        menu.addItem(quit)

        statusItem.menu = menu
    }

    @objc private func toggleWindow() {
        guard let window else { return }
        if window.isVisible {
            window.orderOut(nil)
            toggleItem.title = "Показать"
            onToggle(true)
        } else {
            window.makeKeyAndOrderFront(nil)
            toggleItem.title = "Скрыть"
            onToggle(false)
        }
    }

    @objc private func showHistory() { onShowHistory() }

    @objc private func pickCharacter(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        for item in characterItems { item.state = (item === sender) ? .on : .off }
        onSelectCharacter(id)
    }

    @objc private func pickAppearance(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        for item in appearanceItems { item.state = (item === sender) ? .on : .off }
        onSelectAppearance(id)
    }

    @objc private func quit() {
        NSApp.terminate(nil)
    }
}
