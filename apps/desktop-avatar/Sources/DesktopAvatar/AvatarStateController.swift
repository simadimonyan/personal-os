import AppKit

// Конечный автомат аватара. В покое крутит idle-клип и изредка «оживает»
// (моргание / лёгкая эмоция). На событие pos подбирает реакцию из конфига:
// проигрывает эмоцию, показывает текст, затем возвращается в покой.
final class AvatarStateController {

    private let view: AvatarView
    private var character: CharacterConfig
    private let brain: ChatBrain
    private let agent: AgentBrain
    private var idleTimer: Timer?
    private var busy = false          // сейчас проигрывается эмоция
    private var thinking = false      // ждём ответ мозга на реплику

    init(view: AvatarView, character: CharacterConfig, brain: ChatBrain, agent: AgentBrain) {
        self.view = view
        self.character = character
        self.brain = brain
        self.agent = agent
    }

    func start() {
        goIdle()
        scheduleIdleVariation()
    }

    func stop() {
        idleTimer?.invalidate()
        idleTimer = nil
    }

    func greet() {
        if let line = character.greetings.randomElement() {
            view.bubble.say(line)
        }
    }

    /// Сменить характер: новые реплики, реакции, тайминги.
    func setCharacter(_ c: CharacterConfig) {
        character = c
    }

    // MARK: - Состояния

    private func goIdle() {
        busy = false
        view.play(clip: character.idleClip, loop: true)
    }

    private func playEmotion(_ emotion: String, line: String?) {
        guard let clip = character.emotions[emotion] else { goIdle(); return }
        busy = true
        if let line = line { view.bubble.say(line) }
        view.play(clip: clip, loop: false) { [weak self] in
            self?.goIdle()      // после эмоции — обратно в покой
        }
    }

    // MARK: - Переписка и задачи

    /// Реплика пользователя. Если это задача к системе — выполняет агент (Claude
    /// Code с доступом к ноуту и Personal OS); иначе — быстрый чат в характере.
    /// `completion` (для голосового входа через Siri) получает текст ответа;
    /// на долгих агентских задачах отвечает сразу, не дожидаясь конца — иначе
    /// шорткат отвалится по таймауту, результат всё равно покажется в пузыре.
    func chat(_ message: String, completion: ((String) -> Void)? = nil) {
        guard !thinking else {
            completion?("Секунду, я ещё думаю над прошлым вопросом — спроси чуть позже.")
            return
        }
        thinking = true
        if isSystemTask(message) {
            view.bubble.say("Работаю над этим… ⚙️", seconds: 600)
            completion?("Приняла, работаю над этим. Результат покажу на экране.")
            agent.run(stripBang(message)) { [weak self] reply in self?.finishReply(reply) }
        } else {
            view.bubble.say("…", seconds: 30)      // «печатает», пока ждём ответ
            brain.ask(message) { [weak self] reply in
                self?.finishReply(reply)
                completion?(reply)
            }
        }
    }

    private func finishReply(_ reply: String) {
        thinking = false
        let seconds = max(5.0, Double(reply.count) / 9.0)
        view.bubble.say(reply, seconds: seconds)
        if !busy, let clip = character.emotions["happy"] {
            busy = true
            view.play(clip: clip, loop: false) { [weak self] in self?.goIdle() }
        }
    }

    /// Похоже ли сообщение на задачу к системе (тогда — в агента).
    private func isSystemTask(_ m: String) -> Bool {
        if m.hasPrefix("!") { return true }        // явный префикс — форсим агента
        let l = m.lowercased()
        let triggers = [
            "сделай", "выполни", "запусти", "открой", "создай", "найди", "покажи",
            "проверь", "отправь", "добав", "удали", "переименуй", "собери", "коммит",
            "закоммить", "запиши", "прочитай", "почисти", "синхронизируй", "посчитай",
            "в obsidian", "в обсидиан", "в телеграм", "в telegram", "на hh", "заметку",
            "в системе", "в терминале", "git ", "personal os", "составь",
        ]
        return triggers.contains { l.contains($0) }
    }

    private func stripBang(_ m: String) -> String {
        m.hasPrefix("!") ? String(m.dropFirst()).trimmingCharacters(in: .whitespaces) : m
    }

    // MARK: - События pos

    func handle(event: PosEvent) {
        guard !busy else { return }                       // не перебиваем эмоцию
        guard let reaction = character.eventReactions[event.type] else { return }
        playEmotion(reaction.emotion, line: reaction.lines.randomElement())
    }

    // MARK: - Idle-вариации (аватар «живой» в покое)

    private func scheduleIdleVariation() {
        idleTimer = Timer.scheduledTimer(withTimeInterval: character.idleVariationEverySec,
                                         repeats: true) { [weak self] _ in
            guard let self, !self.busy else { return }
            if let blink = self.character.blinkClip {
                self.view.play(clip: blink, loop: false) { [weak self] in self?.goIdle() }
            }
        }
    }
}
