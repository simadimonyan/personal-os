import AppKit

// Характер аватара, целиком описанный конфигом. Меняешь пресет — меняется
// поведение и реплики, код не трогаешь. Дефолт — «тёплая и игривая».
struct CharacterConfig: Codable {

    struct Reaction: Codable {
        let emotion: String        // ключ клипа из `emotions`
        let lines: [String]        // варианты реплик (выбирается случайная)
    }

    let name: String
    let persona: String?          // «голос» характера для переписки (system-подсказка)
    let accent: String?           // цвет персонажа (hex), напр. "#E8896B"
    let greetings: [String]
    let idleClip: String                     // зацикленный клип покоя
    let blinkClip: String?                   // микровариация (моргание), опц.
    let emotions: [String: String]           // эмоция → имя файла клипа
    let eventReactions: [String: Reaction]   // тип pos-события → реакция
    let idleVariationEverySec: Double        // как часто «оживать» в покое
    let clickThrough: Bool                   // true → мышь проходит «сквозь» аватара

    /// Базовая папка ресурсов. `swift run` стартует из папки пакета, поэтому
    /// по умолчанию берём ./Resources; переопределяется через AVATAR_HOME.
    static var resourcesDirectory: URL {
        if let home = ProcessInfo.processInfo.environment["AVATAR_HOME"] {
            return URL(fileURLWithPath: home).appendingPathComponent("Resources")
        }
        return URL(fileURLWithPath: FileManager.default.currentDirectoryPath)
            .appendingPathComponent("Resources")
    }

    /// У каждого характера — свой набор клипов: Resources/clips/{preset}/.
    /// Мимика и жесты отличаются от пресета к пресету.
    static func clipsDirectory(for preset: String) -> URL {
        resourcesDirectory.appendingPathComponent("clips").appendingPathComponent(preset)
    }

    /// Загрузить пресет из Resources/characters/{preset}.json.
    /// При отсутствии/ошибке — зашитый дефолт, чтобы приложение всегда стартовало.
    static func load(preset: String) -> CharacterConfig {
        let url = resourcesDirectory
            .appendingPathComponent("characters")
            .appendingPathComponent("\(preset).json")
        if let data = try? Data(contentsOf: url),
           let cfg = try? JSONDecoder().decode(CharacterConfig.self, from: data) {
            return cfg
        }
        FileHandle.standardError.write(
            Data("[avatar] пресет '\(preset)' не найден — беру встроенный дефолт\n".utf8))
        return .fallback
    }

    /// «Голос» для переписки: конфиговый persona или мягкий дефолт.
    var voice: String {
        persona ?? "Ты — \(name), дружелюбный настольный компаньон."
    }

    /// Цвет персонажа: из конфига (hex) или тёплый коралловый по умолчанию.
    var tintColor: NSColor {
        NSColor(hex: accent ?? "") ?? NSColor(hex: "#E8896B")!
    }

    static let fallback = CharacterConfig(
        name: "Ава",
        persona: "Ты — Ава, тёплый и игривый настольный компаньон.",
        accent: "#E8896B",
        greetings: ["Привет 👋"],
        idleClip: "idle_breathe.mov",
        blinkClip: "blink.mov",
        emotions: ["happy": "happy.mov", "wave": "wave.mov", "think": "think.mov"],
        eventReactions: [:],
        idleVariationEverySec: 12,
        clickThrough: false
    )
}

extension NSColor {
    /// Цвет из hex-строки вида "#RRGGBB" (или "RRGGBB"). Возвращает nil, если не разобрать.
    convenience init?(hex: String) {
        var s = hex.trimmingCharacters(in: .whitespaces)
        if s.hasPrefix("#") { s.removeFirst() }
        guard s.count == 6, let v = UInt32(s, radix: 16) else { return nil }
        self.init(srgbRed: CGFloat((v >> 16) & 0xFF) / 255,
                  green: CGFloat((v >> 8) & 0xFF) / 255,
                  blue: CGFloat(v & 0xFF) / 255,
                  alpha: 1)
    }
}
