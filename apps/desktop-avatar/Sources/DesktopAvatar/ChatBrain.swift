import Foundation

// «Мозг» переписки. Ходит напрямую в локальный Claude по Unix-сокету
// /tmp/claude_api.sock (сервис claude-local-api). Протокол — построчный JSON:
//   {"prompt": "...", "model": "haiku"}  →  {"result": "...", "worker_ms": 123}
// Сокет однократный/без состояния, поэтому короткую историю держим здесь и
// подмешиваем в промпт, чтобы разговор был связным.
final class ChatBrain {

    private let socketPath = "/tmp/claude_api.sock"
    private var name: String
    private var voice: String
    private let log: ConversationLog?
    private var history: [(user: String, reply: String)] = []
    private let queue = DispatchQueue(label: "avatar.chat.brain")

    init(name: String, voice: String, log: ConversationLog? = nil) {
        self.name = name
        self.voice = voice
        self.log = log
    }

    /// Сменить характер общения (при смене пресета). Сбрасываем контекст диалога.
    func setPersona(name: String, voice: String) {
        self.name = name
        self.voice = voice
        history.removeAll()
    }

    /// Отправить сообщение. Ответ приходит в completion на главном потоке.
    func ask(_ message: String, completion: @escaping (String) -> Void) {
        let who = name
        log?.append(sender: "you", text: message, name: who)
        queue.async { [weak self] in
            guard let self else { return }
            let reply = self.request(for: message)
            self.history.append((message, reply))
            if self.history.count > 16 { self.history.removeFirst(self.history.count - 16) }
            DispatchQueue.main.async {
                self.log?.append(sender: "avatar", text: reply, name: self.name)
                completion(reply)
            }
        }
    }

    // MARK: - Промпт

    private func buildPrompt(_ message: String) -> String {
        let ctx = history.suffix(8)
            .map { "Димитри: \($0.user)\n\(name): \($0.reply)" }
            .joined(separator: "\n")
        let historyBlock = ctx.isEmpty ? "" : "Как шёл разговор до этого:\n\(ctx)\n\n"
        return """
        \(voice)

        Ты — живой настольный компаньон Димитри. Это ОДИН продолжающийся разговор: \
        помни, о чём вы только что говорили, и продолжай его естественно, не начинай \
        с нуля и не здоровайся заново. Отвечай по-человечески и живо, как близкий \
        собеседник — обычно 2–4 предложения, с эмоциями; можешь задать встречный \
        вопрос или отреагировать на настроение. Не будь роботом, без шаблонных фраз \
        и канцелярита. По-русски. Верни только свою реплику — без кавычек и пометок.

        \(historyBlock)Димитри: \(message)
        \(name):
        """
    }

    // MARK: - Unix-сокет

    private func request(for message: String) -> String {
        let payload: [String: Any] = ["prompt": buildPrompt(message), "model": "opus"]  // Opus 4.8 — живые ответы
        guard let json = try? JSONSerialization.data(withJSONObject: payload),
              let raw = send(json) else {
            return "мой мозг сейчас офлайн 😅 (сервис claude-local-api не запущен?)"
        }
        guard let obj = try? JSONSerialization.jsonObject(with: raw) as? [String: Any] else {
            return "…не разобрала ответ."
        }
        if let result = obj["result"] as? String {
            return result.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return (obj["error"] as? String).map { "ошибка: \($0)" } ?? "…что-то пошло не так."
    }

    private func send(_ json: Data) -> Data? {
        let fd = socket(AF_UNIX, SOCK_STREAM, 0)
        guard fd >= 0 else { return nil }
        defer { close(fd) }

        // Ответ Claude может идти несколько секунд — ставим таймаут чтения.
        var tv = timeval(tv_sec: 60, tv_usec: 0)
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &tv, socklen_t(MemoryLayout<timeval>.size))

        var addr = sockaddr_un()
        addr.sun_family = sa_family_t(AF_UNIX)
        let pathLen = MemoryLayout.size(ofValue: addr.sun_path)
        let ok = socketPath.withCString { cstr -> Bool in
            withUnsafeMutablePointer(to: &addr.sun_path) { ptr in
                ptr.withMemoryRebound(to: CChar.self, capacity: pathLen) { dst in
                    strncpy(dst, cstr, pathLen - 1) != nil
                }
            }
        }
        guard ok else { return nil }

        let connected = withUnsafePointer(to: &addr) { p in
            p.withMemoryRebound(to: sockaddr.self, capacity: 1) { sa in
                connect(fd, sa, socklen_t(MemoryLayout<sockaddr_un>.size))
            }
        }
        guard connected == 0 else { return nil }

        var line = json
        line.append(0x0A)                                   // протокол — построчный
        let wrote = line.withUnsafeBytes { write(fd, $0.baseAddress, $0.count) }
        guard wrote > 0 else { return nil }

        var response = Data()
        var buf = [UInt8](repeating: 0, count: 4096)
        while true {
            let n = read(fd, &buf, buf.count)
            if n <= 0 { break }
            response.append(contentsOf: buf[0..<n])
            if buf[0..<n].contains(0x0A) { break }          // дочитали до конца строки
        }
        return response.isEmpty ? nil : response
    }
}
