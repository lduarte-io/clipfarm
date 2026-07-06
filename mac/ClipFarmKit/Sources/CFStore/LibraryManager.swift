import Foundation

/// The library close→swap→reopen path (plan N1). Snapshot-restore,
/// backup-restore (N13), and library switching all reuse it.
///
/// Contract on every transition:
/// 1. The undo stack is cleared FIRST — registered inverses capture the
///    outgoing store and must never fire against a closed/replaced library.
/// 2. The outgoing store is closed.
/// 3. The new store (if any) opens, and `storeDidChange` fires — the hook
///    the app layer uses to restart GRDB ValueObservations (N4+), which are
///    keyed to a store instance and die with it.
/// `@MainActor` because transitions clear the undo stack (`NSUndoManager`
/// is `NS_SWIFT_UI_ACTOR` in the macOS 26 SDK) and because the manager is
/// the app layer's library handle (owned by the MainActor AppStore later).
@MainActor
public final class LibraryManager {
    public private(set) var store: LibraryStore?
    public let undoManager: UndoManager?

    /// Fired after every open/close/swap with the new current store
    /// (nil on close). ValueObservation restart hook for the app layer.
    public var storeDidChange: ((LibraryStore?) -> Void)?

    private let now: @Sendable () -> Date

    public init(
        undoManager: UndoManager? = nil,
        now: @escaping @Sendable () -> Date = Date.init
    ) {
        self.undoManager = undoManager
        self.now = now
    }

    /// Opens the library at `folderURL`, closing any current library first.
    @discardableResult
    public func open(at folderURL: URL) throws -> LibraryStore {
        try closeCurrentStore()
        let opened = try LibraryStore.open(at: folderURL, undoManager: undoManager, now: now)
        store = opened
        storeDidChange?(opened)
        return opened
    }

    /// Alias for `open(at:)` that reads as intent at call sites
    /// (snapshot-restore, backup-restore, switch-library).
    @discardableResult
    public func swap(to folderURL: URL) throws -> LibraryStore {
        try open(at: folderURL)
    }

    public func close() throws {
        try closeCurrentStore()
        storeDidChange?(nil)
    }

    private func closeCurrentStore() throws {
        undoManager?.removeAllActions()
        if let store {
            self.store = nil
            try store.close()
        }
    }
}
