import CFDomain
import SwiftUI

@main
struct ClipFarmApp: App {
    init() {
        // Proves the ClipFarmKit package product links into the app target.
        precondition(CFDomainModule.name == "CFDomain")
    }

    var body: some Scene {
        WindowGroup {
            RootView()
        }
    }
}
