using System.Reflection;
using System.Data.Common;
using System.Security.Cryptography;
using System.Text;
using BackendFork.Infrastructure;

namespace BackendFork.Workforce;

public static class ArgonPin
{
    public static async Task RequireUnique(DbConnection c,DbTransaction t,string pin,string? exceptAccount,CancellationToken ct)
    {
        Validate(pin);
        await WorkforceDb.Execute(c,t,"SELECT pg_advisory_xact_lock(74102851)",ct);
        var rows=await WorkforceDb.Rows(c,t,"SELECT jsonb_build_object('hash',pin_hash)::text FROM app.accounts WHERE pin_hash IS NOT NULL AND (@except IS NULL OR id<>@except)",ct,("except",exceptAccount));
        ApiException.Require(!rows.Any(r=>Verify(pin,r!["hash"]!.GetValue<string>())),"Этот PIN уже назначен другому сотруднику. Выберите другой PIN.",409);
    }
    public static void Validate(string pin) => ApiException.Require(pin.Length==4 && pin.All(c=>c is >= '0' and <= '9'), "PIN должен содержать 4 цифры.");
    private static byte[] Derive(string pin, byte[] salt, int memory, int iterations, int parallel)
    {
        var type=Assembly.Load("Konscious.Security.Cryptography.Argon2").GetType("Konscious.Security.Cryptography.Argon2id")!;
        using var kdf=(DeriveBytes)Activator.CreateInstance(type,Encoding.UTF8.GetBytes(pin))!;
        type.GetProperty("Salt")!.SetValue(kdf,salt);
        type.GetProperty("MemorySize")!.SetValue(kdf,memory);
        type.GetProperty("Iterations")!.SetValue(kdf,iterations);
        type.GetProperty("DegreeOfParallelism")!.SetValue(kdf,parallel);
        return kdf.GetBytes(32);
    }
    public static string Hash(string pin)
    {
        Validate(pin); var salt=RandomNumberGenerator.GetBytes(16);
        return "$argon2id$v=19$m=65536,t=3,p=1$"+Convert.ToBase64String(salt).TrimEnd('=')+"$"+Convert.ToBase64String(Derive(pin,salt,65536,3,1)).TrimEnd('=');
    }
    public static bool Verify(string pin,string? phc)
    {
        Validate(pin); if(phc is null)return false;
        try
        {
            var parts=phc.Split('$'); if(parts.Length!=6 || parts[1]!="argon2id" || parts[2]!="v=19")return false;
            var settings=parts[3].Split(',').Select(x=>x.Split('=')).ToDictionary(x=>x[0],x=>int.Parse(x[1]));
            int m=settings["m"],t=settings["t"],p=settings["p"];
            if(m is <8192 or >262144 || t is <1 or >10 || p is <1 or >8)return false;
            static byte[] Decode(string v)=>Convert.FromBase64String(v+new string('=',(4-v.Length%4)%4));
            return CryptographicOperations.FixedTimeEquals(Derive(pin,Decode(parts[4]),m,t,p),Decode(parts[5]));
        }
        catch(FormatException){return false;} catch(KeyNotFoundException){return false;}
    }
}
