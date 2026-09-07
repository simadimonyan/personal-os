import AppKit
import WebKit

// Облик "holo" — пиксельная девушка-голограмма (стиль JOI, Blade Runner 2049),
// нарисованная процедурно на canvas и живущая внутри прозрачного WKWebView.
// Выражения (idle/happy/curious) и глитч-пульс дёргает AvatarStateController —
// голограмма реагирует на события pos и ответы мозга, как и остальные облики.
//
// Мышь сквозь себя пропускает (hitTest → nil), чтобы окно продолжало таскаться
// за тело (isMovableByWindowBackground) и работал сквозной режим.
final class HologramView: NSView, WKNavigationDelegate {

    private let web: WKWebView
    private var ready = false
    private var pending: [String] = []
    private var motionTimer: Timer?
    private var lastOrigin: CGPoint?

    override init(frame frameRect: NSRect) {
        let cfg = WKWebViewConfiguration()
        web = WKWebView(frame: .zero, configuration: cfg)
        super.init(frame: frameRect)

        wantsLayer = true
        layer?.backgroundColor = .clear

        web.setValue(false, forKey: "drawsBackground")          // прозрачный фон
        if #available(macOS 12.0, *) { web.underPageBackgroundColor = .clear }
        web.navigationDelegate = self
        web.autoresizingMask = [.width, .height]
        addSubview(web)

        let url = CharacterConfig.resourcesDirectory.appendingPathComponent("avatar.html")
        web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) не поддерживается") }

    override func layout() { super.layout(); web.frame = bounds }

    // Клики по голограмме забираем себе (а не в WKWebView) — чтобы таскать окно.
    override func hitTest(_ point: NSPoint) -> NSView? {
        let local = superview?.convert(point, to: self) ?? point
        return bounds.contains(local) ? self : nil
    }
    override var mouseDownCanMoveWindow: Bool { true }
    override func mouseDown(with event: NSEvent) { window?.performDrag(with: event) }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        ready = true
        pending.forEach { web.evaluateJavaScript($0, completionHandler: nil) }
        pending.removeAll()

        // Кормим физику голограммы смещением окна. Таймер в .common-режиме, иначе
        // во время перетаскивания (eventTracking) он бы не срабатывал.
        let tmr = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in self?.pollMotion() }
        RunLoop.main.add(tmr, forMode: .common)
        motionTimer = tmr
    }

    private var lastLook = CGPoint(x: 9, y: 9)   // заведомо «не то» для первой отправки

    private func pollMotion() {
        guard let win = window else { return }
        let o = win.frame.origin
        if let last = lastOrigin {
            let dx = o.x - last.x, dy = o.y - last.y
            if abs(dx) > 0.01 || abs(dy) > 0.01 {
                eval("window.avatarImpulse&&avatarImpulse(\(dx),\(dy))")
            }
        }
        lastOrigin = o

        // Взгляд за курсором: направление от «головы» (верхняя треть окна) к мыши,
        // нормированное в [-1,1]. AppKit y — вверх, в JS инвертируем.
        let mouse = NSEvent.mouseLocation
        let head = CGPoint(x: win.frame.midX, y: win.frame.minY + win.frame.height * 0.72)
        let nx = max(-1, min(1, (mouse.x - head.x) / 420))
        let ny = max(-1, min(1, (mouse.y - head.y) / 420))
        if abs(nx - lastLook.x) > 0.01 || abs(ny - lastLook.y) > 0.01 {
            lastLook = CGPoint(x: nx, y: ny)
            eval("window.avatarLook&&avatarLook(\(nx),\(ny))")
        }
    }

    deinit { motionTimer?.invalidate() }

    private func eval(_ js: String) {
        if ready { web.evaluateJavaScript(js, completionHandler: nil) }
        else { pending.append(js) }
    }

    /// Выражение лица: "idle" | "happy" | "curious".
    func setExpression(_ name: String) { eval("window.avatarSetExpr && avatarSetExpr('\(name)')") }

    /// Короткий глитч-срыв сигнала (на реакцию/ответ).
    func pulse() { eval("window.avatarPulse && avatarPulse()") }

    /// Характер (warm-playful | calm-elegant | bold) — меняет репертуар поз и жестов.
    func setCharacter(_ id: String) { eval("window.avatarSetCharacter && avatarSetCharacter('\(id)')") }
}
