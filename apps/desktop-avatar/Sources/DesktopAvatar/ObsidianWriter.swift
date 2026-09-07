import Foundation

// Пишет переписку в Obsidian как markdown — по файлу на день в
// «10 — Claude/Аватар/Диалоги {дата}.md». Путь к vault берётся из окружения
// AVATAR_VAULT или дефолтный (Органон на Yandex.Disk). Если vault недоступен
// (диск не примонтирован) — тихо ничего не делает.
final class ObsidianWriter {

    private let baseDir: URL?
    private let dayFmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "yyyy-MM-dd"; return f
    }()
    private let timeFmt: DateFormatter = {
        let f = DateFormatter(); f.dateFormat = "HH:mm"; return f
    }()

    init() {
        let defaultVault = "/Users/dimitrisimonyan/Yandex.Disk.localized/Self-Education/Knowledge base/Obsidian/Органон"
        let vault = ProcessInfo.processInfo.environment["AVATAR_VAULT"] ?? defaultVault
        let vaultURL = URL(fileURLWithPath: vault)
        guard FileManager.default.fileExists(atPath: vaultURL.path) else {
            baseDir = nil                          // vault недоступен
            return
        }
        let dir = vaultURL.appendingPathComponent("10 — Claude/Аватар", isDirectory: true)
        try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
        baseDir = dir
    }

    func append(sender: String, text: String, name: String, date: Date) {
        guard let baseDir else { return }
        let day = dayFmt.string(from: date)
        let file = baseDir.appendingPathComponent("Диалоги \(day).md")
        let time = timeFmt.string(from: date)

        var block = ""
        if !FileManager.default.fileExists(atPath: file.path) {
            block += header(day: day)          // frontmatter-маркер для memory-extractor
        }

        switch sender {
        case "action":                          // действие пользователя, не реплика
            block += "> ⚙️ *\(time) — \(text)*\n\n"
        default:
            let who = sender == "you" ? "Ты" : name
            block += "**\(who)** · \(time)\n\n\(text)\n\n"
        }

        if let fh = try? FileHandle(forWritingTo: file) {
            fh.seekToEndOfFile()
            fh.write(Data(block.utf8))
            try? fh.close()
        } else {
            try? Data(block.utf8).write(to: file)
        }
    }

    // Frontmatter помечает файл как L0-источник для memory-extractor и указывает,
    // что реплики «Ты» — вербатим Димитри (источник для [INSIGHT]/[BEHAVIOR]/[VOICE]).
    private func header(day: String) -> String {
        """
        ---
        источник: аватар-компаньон
        тип: L0-диалог
        теги: [аватар, диалог, L0]
        дата: \(day)
        примечание: реплики «Ты» — вербатим Димитри; источник для [INSIGHT]/[BEHAVIOR]/[VOICE]
        ---

        # Диалоги с аватаром — \(day)

        """
    }
}
