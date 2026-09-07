import AppKit

// Сборка приложения: окно-оверлей + вьюха аватара + контроллер состояний +
// мозг переписки + слушатель событий pos + меню-бар. Здесь всё связывается.
final class AppDelegate: NSObject, NSApplicationDelegate {

    private var window: OverlayWindow!
    private var avatarView: AvatarView!
    private var controller: AvatarStateController!
    private var brain: ChatBrain!
    private var agent: AgentBrain!
    private var pos: PosEventListener!
    private var siri: SiriEndpoint!
    private var statusBar: StatusBarController!
    private var passthrough: PassthroughController!
    private var history: HistoryWindowController!
    private var log: ConversationLog!

    private var currentPreset = "warm-playful"
    private let presets: [(id: String, title: String)] = [
        ("warm-playful", "Тёплая и игривая"),
        ("calm-elegant", "Спокойная и элегантная"),
        ("bold", "Дерзкая"),
    ]

    private var currentAppearance = "holo"
    private let appearances: [(id: String, title: String)] = [
        ("holo", "Голограмма (JOI)"),
        ("blob", "Желейный блоб"),
        ("pet", "Питомец (дух-котик)"),
        ("lottie:cactus", "Кактус (смеётся)"),
        ("lottie:wave", "Человечек (машет)"),
    ]

    // Не даём App Nap заморозить рендер (canvas/таймеры), когда фокус в другом окне.
    private var napToken: NSObjectProtocol?

    func applicationDidFinishLaunching(_ notification: Notification) {
        // 0. Держим приложение «активным» для системы, иначе App Nap тормозит
        //    таймеры и рендер WKWebView, когда фокус в другом окне (аватар застывает).
        napToken = ProcessInfo.processInfo.beginActivity(
            options: [.userInitiated, .idleSystemSleepDisabled],
            reason: "desktop avatar rendering")

        // 1. Пресет: из окружения, иначе из сохранённых настроек, иначе дефолт.
        let presetName = ProcessInfo.processInfo.environment["AVATAR_CHARACTER"] ?? loadSetting("character") ?? "warm-playful"
        currentPreset = presetName
        let character = CharacterConfig.load(preset: presetName)

        // Облик: из окружения, иначе из сохранённых настроек, иначе блоб.
        currentAppearance = ProcessInfo.processInfo.environment["AVATAR_APPEARANCE"] ?? loadSetting("appearance") ?? "holo"

        // 2. Небольшое прозрачное окно по размеру аватара.
        window = OverlayWindow()
        let content = window.contentView!

        // 3. Вьюха аватара.
        avatarView = AvatarView(frame: content.bounds,
                                clipsDir: CharacterConfig.clipsDirectory(for: presetName),
                                tint: character.tintColor,
                                appearance: currentAppearance)
        avatarView.autoresizingMask = [.width, .height]
        window.contentView = avatarView
        avatarView.setCharacter(presetName)     // голограмма: репертуар поз по характеру

        // 4. Мозг переписки (локальный Claude по сокету) + лог + контроллер.
        log = ConversationLog()
        history = HistoryWindowController(log: log, name: character.name)
        brain = ChatBrain(name: character.name, voice: character.voice, log: log)
        let projectDir = ProcessInfo.processInfo.environment["AVATAR_PROJECT"] ?? "/Users/dimitrisimonyan/Desktop/personal os"
        agent = AgentBrain(projectDir: projectDir, name: character.name, log: log)
        controller = AvatarStateController(view: avatarView, character: character, brain: brain, agent: agent)
        controller.start()

        // Переписка: ввод пользователя → контроллер → мозг → пузырь.
        avatarView.chatInput.onSubmit = { [weak controller] text in
            controller?.chat(text)
        }

        // 5. Меню-бар: характер, история диалога, скрыть/показать, выключить.
        statusBar = StatusBarController(
            window: window,
            characters: presets,
            current: currentPreset,
            appearances: appearances,
            currentAppearance: currentAppearance,
            onShowHistory: { [weak self] in self?.history.show() },
            onSelectCharacter: { [weak self] id in self?.applyCharacter(preset: id) },
            onSelectAppearance: { [weak self] id in self?.applyAppearance(id) },
            onToggle: { [weak self] hidden in
                self?.log.action(hidden ? "Скрыл аватара" : "Показал аватара")
            })

        // 6. Слушатель событий Personal OS → отдаёт события контроллеру.
        pos = PosEventListener()
        pos.onEvent = { [weak controller] event in
            controller?.handle(event: event)
        }
        pos.start()

        // 6b. Голосовой вход через Siri Shortcuts: HTTP 127.0.0.1:8788/ask.
        //     Тот же путь, что и переписка: контроллер → мозг → пузырь,
        //     плюс ответ уходит обратно в шорткат для озвучки.
        siri = SiriEndpoint()
        siri.onAsk = { [weak controller] text, reply in
            controller?.chat(text, completion: reply)
        }
        siri.start()

        // 7. Сквозной режим: окно ловит мышь только над аватаром.
        passthrough = PassthroughController(window: window, view: avatarView)
        passthrough.start()

        window.orderFrontRegardless()
        // Приветствие — после раскладки, иначе пузырь берёт ещё нулевые размеры
        // и уезжает из центра.
        avatarView.layoutSubtreeIfNeeded()
        DispatchQueue.main.async { [weak self] in self?.controller.greet() }
    }

    // MARK: - Смена характера на лету

    private func applyCharacter(preset: String) {
        guard preset != currentPreset else { return }
        currentPreset = preset
        let c = CharacterConfig.load(preset: preset)
        avatarView.apply(tint: c.tintColor, clipsDir: CharacterConfig.clipsDirectory(for: preset))
        avatarView.setCharacter(preset)
        brain.setPersona(name: c.name, voice: c.voice)
        agent.setName(c.name)
        controller.setCharacter(c)
        saveSetting("character", preset)
        let title = presets.first { $0.id == preset }?.title ?? preset
        log.action("Сменил характер аватара на «\(title)»")
        controller.greet()          // поздороваться в новом характере
    }

    // MARK: - Смена облика на лету (блоб ⇄ питомец)

    private func applyAppearance(_ id: String) {
        guard id != currentAppearance else { return }
        currentAppearance = id
        avatarView.setAppearance(id)
        saveSetting("appearance", id)
        let title = appearances.first { $0.id == id }?.title ?? id
        log.action("Сменил облик аватара на «\(title)»")
    }

    // MARK: - Настройки (запоминаем характер и облик)

    private var settingsURL: URL {
        let base = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("DesktopAvatar", isDirectory: true)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        return base.appendingPathComponent("settings.json")
    }

    private func saveSetting(_ key: String, _ value: String) {
        var obj: [String: Any] = [:]
        if let d = try? Data(contentsOf: settingsURL),
           let existing = try? JSONSerialization.jsonObject(with: d) as? [String: Any] {
            obj = existing
        }
        obj[key] = value
        try? JSONSerialization.data(withJSONObject: obj).write(to: settingsURL)
    }

    private func loadSetting(_ key: String) -> String? {
        guard let d = try? Data(contentsOf: settingsURL),
              let obj = try? JSONSerialization.jsonObject(with: d) as? [String: Any] else { return nil }
        return obj[key] as? String
    }

    func applicationWillTerminate(_ notification: Notification) {
        siri?.stop()
        pos?.stop()
        controller?.stop()
        passthrough?.stop()
    }
}
