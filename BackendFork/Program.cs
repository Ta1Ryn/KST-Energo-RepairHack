using BackendFork.Workforce;
using System.Text.Json.Serialization;
using BackendFork.Auth;
using BackendFork.Infrastructure;
using BackendFork.Services;
using Microsoft.AspNetCore.Authentication;
using Microsoft.AspNetCore.RateLimiting;
using System.Threading.RateLimiting;

var builder = WebApplication.CreateBuilder(args);
builder.Configuration.AddJsonFile("postgres.local.json",optional:true,reloadOnChange:false);
builder.Configuration.AddJsonFile("integration.local.json",optional:true,reloadOnChange:false);
builder.Configuration.AddEnvironmentVariables();
builder.Configuration.AddCommandLine(args);
builder.Logging.ClearProviders();
builder.Logging.AddSimpleConsole();
builder.Services.Configure<BackendOptions>(builder.Configuration.GetSection("Backend"));
builder.Services.Configure<AiOptions>(builder.Configuration.GetSection("Ai"));
builder.Services.AddControllers(options => options.Filters.Add<WorkforceAccessFilter>()).AddJsonOptions(options =>
    options.JsonSerializerOptions.Converters.Add(new JsonStringEnumConverter(System.Text.Json.JsonNamingPolicy.CamelCase)));
builder.Services.AddEndpointsApiExplorer();
builder.Services.AddProblemDetails();
builder.Services.AddExceptionHandler<ApiExceptionHandler>();
builder.Services.Configure<WorkforceOptions>(builder.Configuration.GetSection("Workforce"));
builder.Services.AddHttpContextAccessor();
builder.Services.AddSingleton<WorkforceDb>();
builder.Services.AddSingleton<WorkforceSessions>();
builder.Services.AddScoped<WorkforceAccessFilter>();
builder.Services.Configure<DatabaseOptions>(builder.Configuration.GetSection("Database"));
builder.Services.Configure<ReportOptions>(builder.Configuration.GetSection("Reports"));
var provider = builder.Configuration["Database:Provider"] ?? "Json";
if(builder.Configuration.GetValue<bool>("Workforce:Enabled") && !provider.Equals("PostgreSql",StringComparison.OrdinalIgnoreCase))throw new InvalidOperationException("Workforce integration requires PostgreSql.");
if (provider.Equals("PostgreSql",StringComparison.OrdinalIgnoreCase))
    builder.Services.AddSingleton<IEquipmentRepository, PostgresEquipmentRepository>();
else if (provider.Equals("Json",StringComparison.OrdinalIgnoreCase))
    builder.Services.AddSingleton<IEquipmentRepository, JsonEquipmentRepository>();
else throw new InvalidOperationException("Unknown database provider: " + provider);
builder.Services.AddSingleton<TimeProvider>(TimeProvider.System);
builder.Services.AddSingleton<PinSessionService>();
builder.Services.AddSingleton<EquipmentService>();
builder.Services.AddSingleton<PhotoService>();
builder.Services.AddSingleton<ReferenceService>();
builder.Services.AddSingleton<DocumentService>();
builder.Services.AddSingleton<PdfReportService>();
builder.Services.AddHttpClient<LocalAiClient>((services, client) =>
{
    var options = services.GetRequiredService<Microsoft.Extensions.Options.IOptions<AiOptions>>().Value;
    client.Timeout = TimeSpan.FromSeconds(Math.Clamp(options.TimeoutSeconds, 1, 120));
});
builder.Services.AddAuthentication("PinSession")
    .AddScheme<AuthenticationSchemeOptions, PinAuthenticationHandler>("PinSession", _ => { });
builder.Services.AddAuthorization();
builder.Services.AddRateLimiter(options =>
{
    options.RejectionStatusCode = StatusCodes.Status429TooManyRequests;
    options.AddPolicy("pin", context => RateLimitPartition.GetFixedWindowLimiter(
        context.Connection.RemoteIpAddress?.ToString() ?? "unknown",
        _ => new FixedWindowRateLimiterOptions
        {
            PermitLimit = 5, Window = TimeSpan.FromMinutes(1), QueueLimit = 0, AutoReplenishment = true
        }));
});

var app = builder.Build();
await app.Services.GetRequiredService<IEquipmentRepository>().InitializeAsync();
app.UseExceptionHandler();
app.UseStaticFiles();
app.UseAuthentication();
app.UseAuthorization();
app.UseRateLimiter();
app.MapControllers();
app.MapGet("/health", () => Results.Ok(new { status = "ok", service = "BackendFork" })).AllowAnonymous();
app.MapGet("/openapi/v1.json", (Microsoft.AspNetCore.Mvc.ApiExplorer.IApiDescriptionGroupCollectionProvider explorer) =>
    Results.Json(OpenApiDocument.Create(explorer))).AllowAnonymous();
app.MapGet("/", () => Results.Redirect("/docs.html")).AllowAnonymous();
app.Run();
