/// CFStore — the ONLY seam to the library database (GRDB schema, migrations,
/// snapshots, backup export). Nothing outside this module imports GRDB.
///
/// N0 placeholder. Schema + DatabaseMigrator land at N1.
public enum CFStoreModule {
    public static let name = "CFStore"
}
