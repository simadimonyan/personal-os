import AppKit

// Делает окно «сквозным» везде, кроме самого аватара. Часто (30 Гц) смотрит, где
// курсор: над персонажем/полем/пузырём — окно ловит мышь (ignoresMouseEvents=false),
// иначе пропускает клики в систему (true). Так прозрачные углы окна не мешают
// работать с другими приложениями, а сам аватар остаётся кликабельным.
final class PassthroughController {

    private weak var window: NSWindow?
    private weak var view: AvatarView?
    private var timer: Timer?

    init(window: NSWindow, view: AvatarView) {
        self.window = window
        self.view = view
    }

    func start() {
        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 30.0, repeats: true) { [weak self] _ in
            self?.tick()
        }
    }

    func stop() { timer?.invalidate(); timer = nil }

    private func tick() {
        guard let window, let view else { return }
        let mouseScreen = NSEvent.mouseLocation
        let winPoint = window.convertPoint(fromScreen: mouseScreen)

        let interactive = view.overInteractive(windowPoint: winPoint)
        if window.ignoresMouseEvents != !interactive {
            window.ignoresMouseEvents = !interactive
        }
        view.setHover(interactive)
    }
}
