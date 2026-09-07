import AppKit

// Небольшое прозрачное окно по размеру аватара (не на весь экран — иначе оно
// перехватывало бы клики всего экрана). Видимой рамки нет: фон прозрачный, видно
// только персонажа. Сквозной режим для прозрачных углов делает PassthroughController
// (переключает ignoresMouseEvents по позиции курсора). Двигается перетаскиванием.
final class OverlayWindow: NSWindow {

    static let size = NSSize(width: 300, height: 540)

    init() {
        let vf = NSScreen.main?.visibleFrame ?? NSRect(x: 0, y: 0, width: 1440, height: 900)
        let origin = NSPoint(x: vf.maxX - OverlayWindow.size.width - 30,
                             y: vf.minY + 30)
        super.init(contentRect: NSRect(origin: origin, size: OverlayWindow.size),
                   styleMask: [.borderless],
                   backing: .buffered,
                   defer: false)

        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        level = .floating
        isMovableByWindowBackground = true       // тащим за тело персонажа
        collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
    }

    override var canBecomeKey: Bool { true }
    override var canBecomeMain: Bool { false }
}
