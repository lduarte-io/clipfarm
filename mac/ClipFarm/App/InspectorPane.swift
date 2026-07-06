import SwiftUI

/// The persistent right-side inspector pane (D30) — the slot the preview
/// surface occupies on every page. N0 placeholder; PlayerEngine lands at N2.
struct InspectorPane: View {
    var body: some View {
        ContentUnavailableView("Preview", systemImage: "play.rectangle")
    }
}
