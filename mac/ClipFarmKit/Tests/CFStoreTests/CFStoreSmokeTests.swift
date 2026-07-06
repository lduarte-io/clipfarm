import GRDB
import Testing
@testable import CFStore

@Test func cfStoreModuleLoads() {
    #expect(CFStoreModule.name == "CFStore")
}

/// Smoke-checks that the pinned GRDB dependency links and runs — an in-memory
/// database, no schema, no features.
@Test func grdbOpensAnInMemoryDatabase() throws {
    let dbQueue = try DatabaseQueue()
    let one = try dbQueue.read { db in
        try Int.fetchOne(db, sql: "SELECT 1")
    }
    #expect(one == 1)
}
