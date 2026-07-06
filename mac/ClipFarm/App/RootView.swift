import SwiftUI

struct RootView: View {
    @State private var selection: NavigationItem? = .library
    @State private var isInspectorPresented = true

    var body: some View {
        NavigationSplitView {
            List(NavigationItem.allCases, selection: $selection) { item in
                Label(item.rawValue, systemImage: item.systemImage)
            }
            .navigationSplitViewColumnWidth(min: 160, ideal: 200, max: 280)
        } detail: {
            detail(for: selection ?? .library)
                .inspector(isPresented: $isInspectorPresented) {
                    InspectorPane()
                        .inspectorColumnWidth(min: 280, ideal: 340)
                }
                .toolbar {
                    ToolbarItem {
                        Button {
                            isInspectorPresented.toggle()
                        } label: {
                            Label("Toggle Inspector", systemImage: "sidebar.trailing")
                        }
                    }
                }
        }
    }

    @ViewBuilder
    private func detail(for item: NavigationItem) -> some View {
        switch item {
        case .library: LibraryView()
        case .project: ProjectView()
        case .script: ScriptTOCView()
        case .attempts: AttemptsView()
        case .brief: BriefView()
        case .settings: SettingsView()
        }
    }
}
