import Foundation
import Network

// Голосовой вход через Siri Shortcuts. Мини-HTTP-сервер на 127.0.0.1:8788:
// шорткат «Спроси…» диктует фразу, шлёт её сюда, аватар отвечает тем же мозгом,
// что и в чате (пузырь + эмоция показываются как обычно), а ответ возвращается
// в шорткат — тот проговаривает его через «Озвучить текст».
//
//   GET  /ask?text=привет            → {"reply":"…"}
//   POST /ask  {"text":"привет"}     → {"reply":"…"}
//
// Слушаем только loopback — наружу порт не торчит.
final class SiriEndpoint {

    /// (текст запроса, колбэк с ответом). Колбэк можно звать с любого потока.
    var onAsk: ((String, @escaping (String) -> Void) -> Void)?

    private var listener: NWListener?
    private let queue = DispatchQueue(label: "avatar.siri.endpoint")

    func start() {
        let port = UInt16(ProcessInfo.processInfo.environment["AVATAR_SIRI_PORT"] ?? "") ?? 8788
        let params = NWParameters.tcp
        params.requiredInterfaceType = .loopback
        guard let nwPort = NWEndpoint.Port(rawValue: port),
              let listener = try? NWListener(using: params, on: nwPort) else {
            NSLog("SiriEndpoint: не удалось открыть порт \(port)")
            return
        }
        listener.newConnectionHandler = { [weak self] conn in self?.handle(conn) }
        listener.start(queue: queue)
        self.listener = listener
        NSLog("SiriEndpoint: слушаю http://127.0.0.1:\(port)/ask")
    }

    func stop() {
        listener?.cancel()
        listener = nil
    }

    // MARK: - Соединение

    private func handle(_ conn: NWConnection) {
        conn.start(queue: queue)
        receiveRequest(conn, buffer: Data())
    }

    /// Копим байты, пока не придут все заголовки и тело по Content-Length.
    private func receiveRequest(_ conn: NWConnection, buffer: Data) {
        conn.receive(minimumIncompleteLength: 1, maximumLength: 64 * 1024) { [weak self] data, _, done, error in
            guard let self else { conn.cancel(); return }
            var buf = buffer
            if let data { buf.append(data) }
            if error != nil || buf.count > 256 * 1024 { conn.cancel(); return }

            if let request = Self.parse(buf) {
                self.respond(to: request, over: conn)
            } else if done {
                conn.cancel()
            } else {
                self.receiveRequest(conn, buffer: buf)
            }
        }
    }

    private struct Request { let method: String; let path: String; let body: Data }

    /// nil = запрос ещё не докачан.
    private static func parse(_ data: Data) -> Request? {
        guard let headerEnd = data.range(of: Data("\r\n\r\n".utf8)) else { return nil }
        guard let head = String(data: data[..<headerEnd.lowerBound], encoding: .utf8) else { return nil }
        let lines = head.components(separatedBy: "\r\n")
        let firstLine = lines.first?.components(separatedBy: " ") ?? []
        guard firstLine.count >= 2 else { return nil }

        var contentLength = 0
        for line in lines.dropFirst() {
            let parts = line.split(separator: ":", maxSplits: 1)
            if parts.count == 2, parts[0].lowercased() == "content-length" {
                contentLength = Int(parts[1].trimmingCharacters(in: .whitespaces)) ?? 0
            }
        }
        let body = data[headerEnd.upperBound...]
        guard body.count >= contentLength else { return nil }
        return Request(method: firstLine[0], path: firstLine[1], body: Data(body.prefix(contentLength)))
    }

    // MARK: - Ответ

    private func respond(to request: Request, over conn: NWConnection) {
        guard request.path == "/ask" || request.path.hasPrefix("/ask?") else {
            send(conn, status: "404 Not Found", json: ["error": "unknown path, use /ask"])
            return
        }
        guard let text = extractText(request), !text.isEmpty else {
            send(conn, status: "400 Bad Request", json: ["error": "no text: pass ?text=… or JSON {\"text\":…}"])
            return
        }
        guard let onAsk else {
            send(conn, status: "503 Service Unavailable", json: ["error": "avatar not ready"])
            return
        }
        DispatchQueue.main.async {
            onAsk(text) { [weak self] reply in
                self?.queue.async { self?.send(conn, status: "200 OK", json: ["reply": reply]) }
            }
        }
    }

    private func extractText(_ request: Request) -> String? {
        // GET /ask?text=…
        if let q = URLComponents(string: request.path)?.queryItems,
           let t = q.first(where: { $0.name == "text" })?.value {
            return t.trimmingCharacters(in: .whitespacesAndNewlines)
        }
        // POST: JSON {"text": …} или просто сырой текст в теле
        if !request.body.isEmpty {
            if let obj = try? JSONSerialization.jsonObject(with: request.body) as? [String: Any],
               let t = obj["text"] as? String {
                return t.trimmingCharacters(in: .whitespacesAndNewlines)
            }
            return String(data: request.body, encoding: .utf8)?
                .trimmingCharacters(in: .whitespacesAndNewlines)
        }
        return nil
    }

    private func send(_ conn: NWConnection, status: String, json: [String: String]) {
        let body = (try? JSONSerialization.data(withJSONObject: json)) ?? Data("{}".utf8)
        var response = "HTTP/1.1 \(status)\r\n"
        response += "Content-Type: application/json; charset=utf-8\r\n"
        response += "Content-Length: \(body.count)\r\n"
        response += "Connection: close\r\n\r\n"
        var payload = Data(response.utf8)
        payload.append(body)
        conn.send(content: payload, completion: .contentProcessed { _ in conn.cancel() })
    }
}
