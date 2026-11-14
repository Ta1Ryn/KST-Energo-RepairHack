using System.Data.Common;
using System.Security.Cryptography;
using System.Text.Json.Nodes;
using System.Text.RegularExpressions;

namespace BackendFork.Workforce;

public static class WorkforceMigration
{
    public static async Task Apply(DbConnection c,CancellationToken ct)
    {
        await WorkforceDb.Execute(c,null,"SELECT pg_advisory_lock(74102852)",ct);
        try
        {
            var rows=await WorkforceDb.Rows(c,null,"SELECT jsonb_build_object('version',version)::text FROM app.schema_version ORDER BY version",ct);
            var versions=rows.Select(x=>x!["version"]!.GetValue<int>()).ToArray();
            if(versions.Length==0 || !versions.SequenceEqual(Enumerable.Range(2,versions.Max()-1)) || versions.Max()>7)
                throw new InvalidOperationException("Unsupported schema version history. Expected continuous versions 2..7.");
            await WorkforceDb.Execute(c,null,"CREATE TABLE IF NOT EXISTS app.migration_checksums(version integer PRIMARY KEY REFERENCES app.schema_version(version) ON DELETE RESTRICT,sha256 text NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),origin text NOT NULL CHECK(origin IN ('executed','adopted')),recorded_at timestamptz NOT NULL DEFAULT clock_timestamp())",ct);
            for(int version=2;version<=7;version++)
            {
                string file=version==2?"schema.sql":Directory.GetFiles(Path.Combine(AppContext.BaseDirectory,"Database","migrations"),version.ToString("D3")+"_*.sql").Single(x=>!x.EndsWith(".down.sql"));
                if(version==2)file=Path.Combine(AppContext.BaseDirectory,"Database",file);
                var bytes=await File.ReadAllBytesAsync(file,ct);var hash=Convert.ToHexString(SHA256.HashData(bytes)).ToLowerInvariant();
                var recorded=await WorkforceDb.Scalar(c,null,"SELECT sha256 FROM app.migration_checksums WHERE version=@version::integer",ct,("version",version));
                if(recorded is not null && (string)recorded!=hash)throw new InvalidOperationException("Migration checksum mismatch at version "+version);
                if(versions.Contains(version))
                {
                    if(recorded is null)await WorkforceDb.Execute(c,null,"INSERT INTO app.migration_checksums(version,sha256,origin) VALUES(@version::integer,@hash,'adopted')",ct,("version",version),("hash",hash));
                    continue;
                }
                await using var t=await c.BeginTransactionAsync(ct);
                if(version==6)
                {
                    // Retain the old single-account author identity as a disabled
                    // legacy profile. The migration identifier is not a real HR number.
                    await WorkforceDb.Execute(c,t,"INSERT INTO app.employees(account_id,employee_number,full_name,employment_status) SELECT a.id,'legacy:'||a.id,nullif(a.payload->>'displayName',''),'suspended' FROM app.accounts a WHERE a.account_type='employee' AND a.payload ? 'salt' AND a.payload ? 'hash' AND NOT EXISTS(SELECT 1 FROM app.employees e WHERE e.account_id=a.id)",ct);
                    await WorkforceDb.Execute(c,t,"UPDATE app.accounts SET is_active=false WHERE payload ? 'salt' AND payload ? 'hash'",ct);
                }
                var sql=System.Text.Encoding.UTF8.GetString(bytes).TrimStart('\uFEFF');
                sql=Regex.Replace(sql,@"(?im)^BEGIN\s*;", "",RegexOptions.None,TimeSpan.FromSeconds(1));
                sql=Regex.Replace(sql,@"(?im)^COMMIT\s*;", "",RegexOptions.None,TimeSpan.FromSeconds(1));
                await WorkforceDb.Execute(c,t,"SET LOCAL lock_timeout='15s'; SET LOCAL statement_timeout='5min';\n"+sql,ct);
                await WorkforceDb.Execute(c,t,"INSERT INTO app.migration_checksums(version,sha256,origin) VALUES(@version::integer,@hash,'executed')",ct,("version",version),("hash",hash));
                await t.CommitAsync(ct);
            }
        }
        finally{await WorkforceDb.Execute(c,null,"SELECT pg_advisory_unlock(74102852)",CancellationToken.None);}
    }
}
