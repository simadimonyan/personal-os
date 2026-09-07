import Foundation

// Событие из мира Personal OS, на которое аватар может отреагировать.
struct PosEvent {
    let type: String                 // ключ из eventReactions, напр. "hh_new_response"
    let payload: [String: String]
}

// Слушатель Personal OS. Поллит mission-control (127.0.0.1:8787), сравнивает
// снимки и эмитит события при изменениях. Готовые эндпоинты уже отдают JSON —
// новый бэкенд поднимать не нужно.
final class PosEventListener {

    var onEvent: ((PosEvent) -> Void)?

    private let base = URL(string: "http://127.0.0.1:8787")!
    private let intervalSec: TimeInterval = 30
    private var timer: Timer?
    private var lastDownServices = Set<String>()
    private var lastHHCount: Int?

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: intervalSec, repeats: true) { [weak self] _ in
            self?.poll()
        }
        poll()                       // сразу первый снимок
    }

    func stop() { timer?.invalidate(); timer = nil }

    private func poll() {
        fetch("/api/state") { [weak self] json in self?.diffServices(json) }
        fetch("/api/hh")    { [weak self] json in self?.diffHH(json) }
    }

    // Упавший сервис → «think», поднявшийся → «happy».
    private func diffServices(_ json: [String: Any]) {
        guard let services = json["services"] as? [[String: Any]] else { return }
        let down = Set(services.compactMap { svc -> String? in
            let alive = (svc["alive"] as? Bool) ?? true
            return alive ? nil : (svc["name"] as? String)
        })
        for name in down.subtracting(lastDownServices) {
            onEvent?(PosEvent(type: "service_down", payload: ["service": name]))
        }
        for name in lastDownServices.subtracting(down) {
            onEvent?(PosEvent(type: "service_up", payload: ["service": name]))
        }
        lastDownServices = down
    }

    // Новый отклик на hh.ru → «wave».
    private func diffHH(_ json: [String: Any]) {
        guard let count = json["responses"] as? Int else { return }
        defer { lastHHCount = count }
        if let prev = lastHHCount, count > prev {
            onEvent?(PosEvent(type: "hh_new_response", payload: ["delta": "\(count - prev)"]))
        }
    }

    private func fetch(_ path: String, _ handler: @escaping ([String: Any]) -> Void) {
        let task = URLSession.shared.dataTask(with: base.appendingPathComponent(path)) { data, _, _ in
            guard let data,
                  let json = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            else { return }
            DispatchQueue.main.async { handler(json) }   // события — на главном потоке (UI)
        }
        task.resume()
    }
}
