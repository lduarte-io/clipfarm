import SwiftUI

@main
struct ClipFarmApp: App {
    // No global singletons: the store is created here and injected via
    // Environment (mac/CLAUDE.md invariant).
    @State private var appStore = AppStore()

    var body: some Scene {
        WindowGroup {
            RootView()
                .environment(appStore)
        }
    }
}
