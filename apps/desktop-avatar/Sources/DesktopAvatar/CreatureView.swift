import AppKit

// Стилизованный персонаж с физикой (не фотореализм — тот криповый). Живой:
// дышит в покое, моргает, следит глазами за курсором, а при перетаскивании окна
// отстаёт по инерции, тянется по направлению движения (squash & stretch) и
// пружинит обратно с затуханием.
//
// Облик переключается на лету (меню-бар → «Облик»):
//   • .blob — мягкая желейная капля (исходный облик);
//   • .pet  — дух-котик: тело-яйцо, ушки, большие глаза, носик, усы, хвост.
// Физический движок общий для обоих обликов — меняются только фигуры.
final class CreatureView: NSView {

    enum Appearance: String {
        case blob
        case pet
    }

    // Пружинная физика тела относительно точки покоя.
    private var offset = CGVector.zero        // смещение тела от центра
    private var velocity = CGVector.zero
    private var lastWindowOrigin: CGPoint?
    private var phase: CGFloat = 0            // фаза дыхания
    private var blinkT: CGFloat = 0          // прогресс моргания 0..1
    private var nextBlink: CGFloat = 2.4
    private var clock: CGFloat = 0
    private var timer: Timer?

    private var tint: NSColor
    private var look: Appearance = .blob

    // Общие слои.
    private let body = CAShapeLayer()
    private let highlight = CAShapeLayer()
    private let leftEye = CALayer()
    private let rightEye = CALayer()
    private let leftPupil = CALayer()
    private let rightPupil = CALayer()
    private let mouth = CAShapeLayer()

    // Слои только для облика .pet (сабслои body → двигаются вместе с телом).
    private let leftEar = CAShapeLayer()
    private let rightEar = CAShapeLayer()
    private let leftEarInner = CAShapeLayer()
    private let rightEarInner = CAShapeLayer()
    private let nose = CAShapeLayer()
    private let whiskers = CAShapeLayer()
    private let tail = CAShapeLayer()
    private var petLayers: [CAShapeLayer] { [leftEar, rightEar, leftEarInner, rightEarInner, nose, whiskers, tail] }

    init(frame: NSRect, tint: NSColor, appearance: Appearance = .blob) {
        self.tint = tint
        self.look = appearance
        super.init(frame: frame)
        wantsLayer = true
        layer?.backgroundColor = .clear

        body.fillColor = tint.cgColor
        body.shadowColor = NSColor.black.cgColor
        body.shadowOpacity = 0.28
        body.shadowRadius = 12
        body.shadowOffset = CGSize(width: 0, height: -4)
        layer?.addSublayer(body)

        // Хвост — под телом по z-порядку, но сабслой body, чтобы жить общей физикой.
        tail.fillColor = tint.cgColor
        body.addSublayer(tail)

        // Ушки (за телом сверху).
        for ear in [leftEar, rightEar] {
            ear.fillColor = tint.cgColor
            body.addSublayer(ear)
        }
        leftEar.addSublayer(leftEarInner)
        rightEar.addSublayer(rightEarInner)
        for inner in [leftEarInner, rightEarInner] {
            inner.fillColor = NSColor(calibratedRed: 1.0, green: 0.72, blue: 0.78, alpha: 0.9).cgColor
        }

        highlight.fillColor = NSColor.white.withAlphaComponent(0.18).cgColor
        body.addSublayer(highlight)

        for eye in [leftEye, rightEye] {
            eye.backgroundColor = NSColor.white.cgColor
            body.addSublayer(eye)
        }
        for pupil in [leftPupil, rightPupil] {
            pupil.backgroundColor = NSColor(calibratedWhite: 0.12, alpha: 1).cgColor
        }
        leftEye.addSublayer(leftPupil)
        rightEye.addSublayer(rightPupil)

        // Носик (pet).
        nose.fillColor = NSColor(calibratedRed: 0.95, green: 0.55, blue: 0.62, alpha: 1).cgColor
        body.addSublayer(nose)

        // Усы (pet).
        whiskers.fillColor = NSColor.clear.cgColor
        whiskers.strokeColor = NSColor(calibratedWhite: 0.25, alpha: 0.55).cgColor
        whiskers.lineWidth = 1.5
        whiskers.lineCap = .round
        body.addSublayer(whiskers)

        // Рот (blob).
        mouth.fillColor = NSColor.clear.cgColor
        mouth.strokeColor = NSColor(calibratedWhite: 0.15, alpha: 0.8).cgColor
        mouth.lineWidth = 3
        mouth.lineCap = .round
        body.addSublayer(mouth)

        applyAppearanceVisibility()

        timer = Timer.scheduledTimer(withTimeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            self?.step()
        }
    }

    required init?(coder: NSCoder) { fatalError("init(coder:) не поддерживается") }
    deinit { timer?.invalidate() }

    // MARK: - Смена облика на лету

    func setAppearance(_ a: Appearance) {
        guard a != look else { return }
        look = a
        applyAppearanceVisibility()
        needsLayout = true
        layoutStatics()
        react()          // маленький «прыжок» при перевоплощении
    }

    private func applyAppearanceVisibility() {
        let pet = look == .pet
        petLayers.forEach { $0.isHidden = !pet }
        mouth.isHidden = pet
    }

    // MARK: - Раскладка статичных элементов

    override func layout() {
        super.layout()
        layoutStatics()
    }

    private func layoutStatics() {
        let w = bounds.width, h = bounds.height
        body.frame = bounds
        body.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        body.position = CGPoint(x: w / 2, y: h / 2)
        body.bounds = bounds

        switch look {
        case .blob: layoutBlob(w: w, h: h)
        case .pet:  layoutPet(w: w, h: h)
        }
    }

    // Исходный облик — мягкая «капля».
    private func layoutBlob(w: CGFloat, h: CGFloat) {
        let bodyRect = CGRect(x: w * 0.14, y: h * 0.10, width: w * 0.72, height: h * 0.80)
        body.path = CGPath(ellipseIn: bodyRect, transform: nil)
        highlight.path = CGPath(ellipseIn: CGRect(x: bodyRect.minX + bodyRect.width * 0.18,
                                                  y: bodyRect.midY + bodyRect.height * 0.05,
                                                  width: bodyRect.width * 0.5,
                                                  height: bodyRect.height * 0.38), transform: nil)

        let eyeW = w * 0.15, eyeH = eyeW * 1.15
        let eyeY = h * 0.52
        let dx = w * 0.14
        layoutEyes(w: w, eyeW: eyeW, eyeH: eyeH, eyeY: eyeY, dx: dx)

        let mp = CGMutablePath()
        let mw = w * 0.16, my = h * 0.40, mx = w / 2
        mp.move(to: CGPoint(x: mx - mw / 2, y: my))
        mp.addQuadCurve(to: CGPoint(x: mx + mw / 2, y: my),
                        control: CGPoint(x: mx, y: my - h * 0.05))
        mouth.path = mp
    }

    // Дух-котик: тело-яйцо, ушки, большие глаза, носик, усы, хвост.
    private func layoutPet(w: CGFloat, h: CGFloat) {
        // Тело — чуть ниже, чтобы сверху поместились ушки.
        let bodyRect = CGRect(x: w * 0.17, y: h * 0.06, width: w * 0.66, height: h * 0.74)
        body.path = CGPath(roundedRect: bodyRect,
                           cornerWidth: bodyRect.width * 0.45,
                           cornerHeight: bodyRect.height * 0.45, transform: nil)
        highlight.path = CGPath(ellipseIn: CGRect(x: bodyRect.minX + bodyRect.width * 0.20,
                                                  y: bodyRect.minY + bodyRect.height * 0.10,
                                                  width: bodyRect.width * 0.42,
                                                  height: bodyRect.height * 0.30), transform: nil)

        // Ушки — треугольники поверх тела.
        func earPath(cx: CGFloat) -> CGMutablePath {
            let p = CGMutablePath()
            let baseY = bodyRect.maxY - h * 0.06
            p.move(to: CGPoint(x: cx - w * 0.11, y: baseY - h * 0.02))
            p.addLine(to: CGPoint(x: cx, y: baseY + h * 0.20))       // остриё
            p.addLine(to: CGPoint(x: cx + w * 0.11, y: baseY + h * 0.02))
            p.closeSubpath()
            return p
        }
        let leftCx = w * 0.36, rightCx = w * 0.64
        leftEar.path = earPath(cx: leftCx)
        rightEar.path = earPath(cx: rightCx)
        // Внутреннее ухо — уменьшенная копия.
        func innerPath(cx: CGFloat) -> CGMutablePath {
            let p = CGMutablePath()
            let baseY = bodyRect.maxY - h * 0.05
            p.move(to: CGPoint(x: cx - w * 0.055, y: baseY))
            p.addLine(to: CGPoint(x: cx, y: baseY + h * 0.12))
            p.addLine(to: CGPoint(x: cx + w * 0.055, y: baseY))
            p.closeSubpath()
            return p
        }
        leftEarInner.frame = bounds; leftEarInner.path = innerPath(cx: leftCx)
        rightEarInner.frame = bounds; rightEarInner.path = innerPath(cx: rightCx)

        // Глаза — крупнее, кошачьи.
        let eyeW = w * 0.17, eyeH = eyeW * 1.2
        let eyeY = h * 0.42
        let dx = w * 0.155
        layoutEyes(w: w, eyeW: eyeW, eyeH: eyeH, eyeY: eyeY, dx: dx)

        // Носик — маленький перевёрнутый треугольник по центру.
        let np = CGMutablePath()
        let nx = w / 2, ny = h * 0.36, nw = w * 0.05
        np.move(to: CGPoint(x: nx - nw, y: ny + nw * 0.7))
        np.addLine(to: CGPoint(x: nx + nw, y: ny + nw * 0.7))
        np.addLine(to: CGPoint(x: nx, y: ny - nw * 0.7))
        np.closeSubpath()
        nose.path = np

        // Усы — по 2 с каждой стороны от носа.
        let wp = CGMutablePath()
        for side in [CGFloat(-1), CGFloat(1)] {
            let x0 = nx + side * w * 0.05
            for (i, dyy) in [CGFloat(0.02), CGFloat(-0.03)].enumerated() {
                let y0 = ny + w * 0.01 + CGFloat(i) * 0
                wp.move(to: CGPoint(x: x0, y: y0))
                wp.addLine(to: CGPoint(x: x0 + side * w * 0.24, y: y0 + h * dyy))
            }
        }
        whiskers.path = wp

        // Хвост — изогнутый росчерк от низа-справа тела.
        let tp = CGMutablePath()
        let tw = w * 0.10
        let start = CGPoint(x: bodyRect.maxX - w * 0.04, y: bodyRect.minY + h * 0.06)
        tp.move(to: start)
        tp.addCurve(to: CGPoint(x: w * 0.98, y: h * 0.30),
                    control1: CGPoint(x: w * 1.02, y: h * 0.06),
                    control2: CGPoint(x: w * 1.04, y: h * 0.26))
        tp.addCurve(to: CGPoint(x: start.x, y: start.y + tw),
                    control1: CGPoint(x: w * 0.94, y: h * 0.20),
                    control2: CGPoint(x: bodyRect.maxX - w * 0.02, y: start.y + tw * 1.6))
        tp.closeSubpath()
        tail.path = tp
    }

    // Общая раскладка глаз и зрачков.
    private func layoutEyes(w: CGFloat, eyeW: CGFloat, eyeH: CGFloat, eyeY: CGFloat, dx: CGFloat) {
        for (eye, sign) in [(leftEye, -CGFloat(1)), (rightEye, CGFloat(1))] {
            eye.frame = CGRect(x: w / 2 + sign * dx - eyeW / 2, y: eyeY, width: eyeW, height: eyeH)
            eye.cornerRadius = eyeW / 2
            eye.masksToBounds = true
        }
        let pupilD = eyeW * 0.52
        for (pupil, eye) in [(leftPupil, leftEye), (rightPupil, rightEye)] {
            pupil.frame = CGRect(x: (eye.bounds.width - pupilD) / 2,
                                 y: (eye.bounds.height - pupilD) / 2,
                                 width: pupilD, height: pupilD)
            pupil.cornerRadius = pupilD / 2
        }
    }

    // MARK: - Реакция на эмоцию (радость): подпрыгнуть

    func react() {
        velocity.dy += 620
        velocity.dx += CGFloat.random(in: -120...120)
    }

    /// Толчок при перетаскивании аватара — тело отстаёт по инерции.
    func kick(dx: CGFloat, dy: CGFloat) {
        velocity.dx -= dx * 9
        velocity.dy -= dy * 9
    }

    /// Сменить цвет персонажа (при смене характера).
    func setTint(_ color: NSColor) {
        tint = color
        body.fillColor = color.cgColor
        leftEar.fillColor = color.cgColor
        rightEar.fillColor = color.cgColor
        tail.fillColor = color.cgColor
    }

    // MARK: - Кадр физики

    private func step() {
        let dt: CGFloat = 1.0 / 60.0
        clock += dt
        phase += dt

        // Инерция от перетаскивания окна: тело отстаёт и пружинит.
        if let win = window {
            let o = win.frame.origin
            if let last = lastWindowOrigin {
                let dx = o.x - last.x, dy = o.y - last.y
                if dx != 0 || dy != 0 { velocity.dx -= dx * 9; velocity.dy -= dy * 9 }
            }
            lastWindowOrigin = o
        }

        // Пружина к точке покоя с затуханием.
        let k: CGFloat = 190, damp: CGFloat = 13
        velocity.dx += (-k * offset.dx - damp * velocity.dx) * dt
        velocity.dy += (-k * offset.dy - damp * velocity.dy) * dt
        offset.dx += velocity.dx * dt
        offset.dy += velocity.dy * dt

        // 3. Дыхание в покое.
        let breathe = sin(phase * 1.6) * 0.02
        let bob = sin(phase * 1.6) * 2.0

        // 4. Squash & stretch по направлению скорости.
        let speed = hypot(velocity.dx, velocity.dy)
        let s = min(speed / 900, 0.32)
        let angle = atan2(velocity.dy, velocity.dx)

        var tf = CGAffineTransform.identity
        tf = tf.translatedBy(x: offset.dx, y: offset.dy + bob)
        tf = tf.rotated(by: angle)
        tf = tf.scaledBy(x: 1 + s + breathe, y: 1 - s + breathe)
        tf = tf.rotated(by: -angle)

        CATransaction.begin()
        CATransaction.setDisableActions(true)
        body.setAffineTransform(tf)
        updateEyes(dt: dt)
        CATransaction.commit()
    }

    // Зрачки следят за курсором; периодическое моргание.
    private func updateEyes(dt: CGFloat) {
        // Направление на курсор в координатах экрана.
        var look = CGVector(dx: velocity.dx, dy: velocity.dy)
        if let win = window {
            let mouse = NSEvent.mouseLocation
            let center = CGPoint(x: win.frame.midX, y: win.frame.minY + bounds.midY)
            look = CGVector(dx: mouse.x - center.x, dy: mouse.y - center.y)
        }
        let len = max(1, hypot(look.dx, look.dy))
        let amp: CGFloat = 3.5
        let px = look.dx / len * amp, py = look.dy / len * amp
        for (pupil, eye) in [(leftPupil, leftEye), (rightPupil, rightEye)] {
            let d = pupil.bounds.width
            pupil.position = CGPoint(x: eye.bounds.width / 2 + px, y: eye.bounds.height / 2 + py)
            _ = d
        }

        // Моргание.
        nextBlink -= dt
        if nextBlink <= 0 && blinkT == 0 { blinkT = 0.0001 }
        if blinkT > 0 {
            blinkT += dt * 6
            let openness: CGFloat = blinkT < 1 ? abs(1 - 2 * min(blinkT, 1)) : 1
            for eye in [leftEye, rightEye] {
                eye.transform = CATransform3DMakeScale(1, max(0.08, openness), 1)
            }
            if blinkT >= 1 { blinkT = 0; nextBlink = CGFloat.random(in: 2.0...4.5) }
        }
    }
}
