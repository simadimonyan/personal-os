import Foundation

// Полная история переписки с аватаром. Хранится в памяти и дописывается в файл
// (~/Library/Application Support/DesktopAvatar/history.jsonl), чтобы переживать
// перезапуск. Отсюда её читает окно «История диалога».
struct ChatEntry: Codable {
    let sender: String        // "you" | "avatar"
    let text: String
    let ts: Date
}

final class ConversationLog {

    private(set) var entries: [ChatEntry] = []
    private let fileURL: URL
    private let obsidian = ObsidianWriter()

    init() {
        let base = FileManager.default
            .urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("DesktopAvatar", isDirectory: true)
        try? FileManager.default.createDirectory(at: base, withIntermediateDirectories: true)
        fileURL = base.appendingPathComponent("history.jsonl")
        load()
    }

    /// Записать действие пользователя (сменил характер, скрыл аватара и т.п.).
    func action(_ text: String) {
        append(sender: "action", text: text)
    }

    func append(sender: String, text: String, name: String = "Ава") {
        let entry = ChatEntry(sender: sender, text: text, ts: Date())
        entries.append(entry)
        obsidian.append(sender: sender, text: text, name: name, date: entry.ts)
        guard let data = try? JSONEncoder().encode(entry),
              var line = String(data: data, encoding: .utf8) else { return }
        line += "\n"
        if let fh = try? FileHandle(forWritingTo: fileURL) {
            fh.seekToEndOfFile()
            fh.write(Data(line.utf8))
            try? fh.close()
        } else {
            try? Data(line.utf8).write(to: fileURL)
        }
    }

    private func load() {
        guard let content = try? String(contentsOf: fileURL, encoding: .utf8) else { return }
        for line in content.split(separator: "\n") {
            if let entry = try? JSONDecoder().decode(ChatEntry.self, from: Data(line.utf8)) {
                entries.append(entry)
            }
        }
    }
}
