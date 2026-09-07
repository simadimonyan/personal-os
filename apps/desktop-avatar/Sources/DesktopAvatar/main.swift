import AppKit

// Точка входа. Аватар — accessory-приложение: без иконки в Dock, живёт оверлеем.
let app = NSApplication.shared
app.setActivationPolicy(.accessory)

let delegate = AppDelegate()
app.delegate = delegate
app.run()
