import Foundation

// Агентный «мозг» для задач с системой. В отличие от ChatBrain (быстрый haiku
// без инструментов), запускает НАСТОЯЩИЙ Claude Code (`claude -p`) в папке
// Personal OS — с доступом к bash, файлам, скиллам и агентам. Так аватар может
// РАБОТАТЬ с ноутом и Personal OS, если его попросить.
//
// ВНИМАНИЕ: работает с --dangerously-skip-permissions (иначе задача зависнет на
// запросе подтверждения — у аватара нет UI для approve). То есть выполняет
// команды в системе без спроса. Это по явному запросу владельца.
final class AgentBrain {

    private let projectDir: String
    private let claudePath: String
    private let log: ConversationLog?
    private var name: String
    private let queue = DispatchQueue(label: "avatar.agent")
    private let timeout: TimeInterval = 300

    init(projectDir: String, name: String, log: ConversationLog?) {
        self.projectDir = projectDir
        self.name = name
        self.log = log
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = ["\(home)/.local/bin/claude", "/opt/homebrew/bin/claude", "/usr/local/bin/claude"]
        claudePath = candidates.first { FileManager.default.isExecutableFile(atPath: $0) } ?? "claude"
    }

    func setName(_ n: String) { name = n }

    /// Выполнить задачу. Ответ (краткий отчёт) приходит на главном потоке.
    func run(_ task: String, completion: @escaping (String) -> Void) {
        log?.append(sender: "you", text: task, name: name)
        queue.async { [weak self] in
            guard let self else { return }
            let reply = self.exec(task)
            DispatchQueue.main.async {
                self.log?.append(sender: "avatar", text: reply, name: self.name)
                completion(reply)
            }
        }
    }

    private func exec(_ task: String) -> String {
        let prompt = """
        Ты — ассистент внутри десктоп-аватара Димитри с доступом к его системе \
        Personal OS (bash, файлы, скиллы, агенты). Выполни задачу пользователя, \
        используя инструменты. В конце ответь КРАТКО (1–3 предложения по-русски), \
        что сделал или что нашёл — этот текст покажется в пузыре над аватаром.
        Задача: \(task)
        """

        let p = Process()
        p.executableURL = URL(fileURLWithPath: claudePath)
        p.arguments = ["-p", prompt, "--model", "opus", "--dangerously-skip-permissions", "--output-format", "text"]
        p.currentDirectoryURL = URL(fileURLWithPath: projectDir)

        var env = ProcessInfo.processInfo.environment
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        env["PATH"] = "\(home)/.local/bin:/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin"
        p.environment = env

        let outPipe = Pipe()
        p.standardOutput = outPipe
        p.standardError = FileHandle.nullDevice

        do { try p.run() } catch {
            return "не смог запустить claude: \(error.localizedDescription)"
        }

        // Сторож: если задача зависла — прибиваем через timeout.
        let killer = DispatchWorkItem { if p.isRunning { p.terminate() } }
        DispatchQueue.global().asyncAfter(deadline: .now() + timeout, execute: killer)

        let data = outPipe.fileHandleForReading.readDataToEndOfFile()
        p.waitUntilExit()
        killer.cancel()

        let out = String(data: data, encoding: .utf8)?
            .trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if out.isEmpty {
            return p.terminationStatus == 0 ? "Готово." : "Не получилось выполнить (код \(p.terminationStatus))."
        }
        return out
    }
}
