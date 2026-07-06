import CFDomain
import CFStore
import SwiftUI

/// Settings page — N3 scope: the D18 per-library segmentation settings
/// (they live in the library database and travel with it). Provider/model,
/// export defaults, and library location land N7+.
struct SettingsView: View {
    @Environment(AppStore.self) private var appStore

    var body: some View {
        Form {
            Section("Segmentation") {
                LabeledContent("Silence threshold") {
                    HStack {
                        Slider(value: silenceThreshold, in: 0.25...5.0, step: 0.25)
                            .frame(maxWidth: 220)
                        Text("\(appStore.librarySettings.silenceThresholdSec, format: .number.precision(.fractionLength(2))) s")
                            .monospacedDigit()
                            .frame(width: 56, alignment: .trailing)
                    }
                }
                .help("A new clip starts when the gap between words is at least this long")

                Picker("Clip tail", selection: tailPolicy) {
                    Text("Extend to next word (default)")
                        .tag(SegmentationTailPolicy.extendToNextWordStart)
                    Text("Fixed padding").tag(SegmentationTailPolicy.fixedPadding)
                    Text("Word end (web behavior)").tag(SegmentationTailPolicy.wordEnd)
                }
                .help("How far a clip's end reaches past its last spoken word")

                if appStore.librarySettings.tailPolicy == .fixedPadding {
                    LabeledContent("Tail padding") {
                        HStack {
                            Slider(value: tailPadding, in: 0.0...1.0, step: 0.05)
                                .frame(maxWidth: 220)
                            Text("\(appStore.librarySettings.tailPaddingSec, format: .number.precision(.fractionLength(2))) s")
                                .monospacedDigit()
                                .frame(width: 56, alignment: .trailing)
                        }
                    }
                }
            }
            Section {
                Text(
                    "Changing these settings does not touch existing clips. "
                        + "Use a source's “Re-apply Segmentation” action in the "
                        + "Library to recompute its auto-detected clips — "
                        + "hand-corrected clips are never overwritten, and the "
                        + "action is undoable."
                )
                .font(.callout)
                .foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
    }

    // MARK: - Bindings (views mutate only through store methods)

    private var silenceThreshold: Binding<Double> {
        Binding(
            get: { appStore.librarySettings.silenceThresholdSec },
            set: { newValue in update { $0.silenceThresholdSec = newValue } }
        )
    }

    private var tailPolicy: Binding<SegmentationTailPolicy> {
        Binding(
            get: { appStore.librarySettings.tailPolicy },
            set: { newValue in update { $0.tailPolicy = newValue } }
        )
    }

    private var tailPadding: Binding<Double> {
        Binding(
            get: { appStore.librarySettings.tailPaddingSec },
            set: { newValue in update { $0.tailPaddingSec = newValue } }
        )
    }

    private func update(_ mutate: (inout LibrarySettings) -> Void) {
        var settings = appStore.librarySettings
        mutate(&settings)
        appStore.updateLibrarySettings(settings)
    }
}
