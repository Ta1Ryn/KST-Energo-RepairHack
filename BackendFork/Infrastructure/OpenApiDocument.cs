using System.ComponentModel.DataAnnotations;
using System.Reflection;
using System.Text.Json;
using Microsoft.AspNetCore.Authorization;
using Microsoft.AspNetCore.Mvc.ApiExplorer;
using Microsoft.AspNetCore.Mvc.ModelBinding;

namespace BackendFork.Infrastructure;

// Framework-only OpenAPI generation: no CDN or NuGet dependencies.
public static class OpenApiDocument
{
    public static object Create(IApiDescriptionGroupCollectionProvider explorer)
    {
        var schemas = new Dictionary<string, object>();
        var paths = new Dictionary<string, Dictionary<string, object>>();
        var nullability = new NullabilityInfoContext();
        object Schema(Type type)
        {
            var underlying = Nullable.GetUnderlyingType(type);
            if (underlying is not null)
            {
                var result = new Dictionary<string, object>((Dictionary<string, object>)Schema(underlying)) { ["nullable"] = true };
                return result;
            }
            if (type == typeof(IFormFile)) return new Dictionary<string, object> { ["type"] = "string", ["format"] = "binary" };
            if (type == typeof(string)) return new Dictionary<string, object> { ["type"] = "string" };
            if (type == typeof(Guid)) return new Dictionary<string, object> { ["type"] = "string", ["format"] = "uuid" };
            if (type == typeof(DateTimeOffset) || type == typeof(DateTime))
                return new Dictionary<string, object> { ["type"] = "string", ["format"] = "date-time" };
            if (type == typeof(bool)) return new Dictionary<string, object> { ["type"] = "boolean" };
            if (type.IsEnum) return new Dictionary<string, object>
            {
                ["type"] = "string", ["enum"] = Enum.GetNames(type).Select(JsonNamingPolicy.CamelCase.ConvertName).ToArray()
            };
            if (type == typeof(int) || type == typeof(long) || type == typeof(short))
                return new Dictionary<string, object> { ["type"] = "integer", ["format"] = type == typeof(long) ? "int64" : "int32" };
            if (type == typeof(decimal) || type == typeof(double) || type == typeof(float))
                return new Dictionary<string, object> { ["type"] = "number" };
            if (type.IsGenericType && type.GetGenericTypeDefinition() == typeof(Dictionary<,>))
                return new Dictionary<string, object> { ["type"] = "object", ["additionalProperties"] = Schema(type.GenericTypeArguments[1]) };
            if (type.IsArray) return new Dictionary<string, object> { ["type"] = "array", ["items"] = Schema(type.GetElementType()!) };
            var enumerable = type.GetInterfaces().Append(type).FirstOrDefault(t => t.IsGenericType && t.GetGenericTypeDefinition() == typeof(IEnumerable<>));
            if (enumerable is not null)
                return new Dictionary<string, object> { ["type"] = "array", ["items"] = Schema(enumerable.GenericTypeArguments[0]) };
            var name = type.IsGenericType ? type.Name.Split('`')[0] + string.Join("", type.GenericTypeArguments.Select(t => t.Name)) : type.Name;
            if (!schemas.ContainsKey(name))
            {
                schemas[name] = new Dictionary<string, object>();
                var props = new Dictionary<string, object>();
                var required = new List<string>();
                foreach (var prop in type.GetProperties(BindingFlags.Instance | BindingFlags.Public).Where(p => p.GetIndexParameters().Length == 0))
                {
                    var key = JsonNamingPolicy.CamelCase.ConvertName(prop.Name);
                    var schema = (Dictionary<string, object>)Schema(prop.PropertyType);
                    var nullable = Nullable.GetUnderlyingType(prop.PropertyType) is not null ||
                        (!prop.PropertyType.IsValueType && nullability.Create(prop).ReadState == NullabilityState.Nullable);
                    if (nullable && schema.ContainsKey("$ref"))
                        schema = new Dictionary<string, object> { ["allOf"] = new[] { schema }, ["nullable"] = true };
                    else if (nullable) schema["nullable"] = true;
                    props[key] = schema;
                    if (prop.GetCustomAttribute<RequiredAttribute>() is not null || !nullable) required.Add(key);
                    if (prop.GetCustomAttribute<StringLengthAttribute>() is { } length)
                    { schema["maxLength"] = length.MaximumLength; if (length.MinimumLength > 0) schema["minLength"] = length.MinimumLength; }
                    if (prop.GetCustomAttribute<RegularExpressionAttribute>() is { } pattern) schema["pattern"] = pattern.Pattern;
                }
                schemas[name] = new Dictionary<string, object> { ["type"] = "object", ["properties"] = props, ["required"] = required };
            }
            return new Dictionary<string, object> { ["$ref"] = "#/components/schemas/" + name };
        }
        foreach (var api in explorer.ApiDescriptionGroups.Items.SelectMany(g => g.Items))
        {
            if (api.HttpMethod is null || api.RelativePath is null || !api.RelativePath.StartsWith("api/")) continue;
            var path = "/" + api.RelativePath.Split('?')[0];
            var operation = new Dictionary<string, object>
            {
                ["operationId"] = api.ActionDescriptor.RouteValues["controller"] + "_" +
                    api.ActionDescriptor.RouteValues["action"],
                ["tags"] = new[] { api.ActionDescriptor.RouteValues["controller"] ?? "API" }
            };
            if (!api.ActionDescriptor.EndpointMetadata.OfType<AllowAnonymousAttribute>().Any())
                operation["security"] = new[] { new Dictionary<string, string[]> { ["pinSession"] = [] } };
            var parameters = new List<object>();
            var form = new Dictionary<string, object>();
            foreach (var parameter in api.ParameterDescriptions)
            {
                if (parameter.Type == typeof(CancellationToken)) continue;
                if (parameter.Source == BindingSource.Body)
                    operation["requestBody"] = new
                    {
                        required = true,
                        content = new Dictionary<string, object> { ["application/json"] = new { schema = Schema(parameter.Type) } }
                    };
                else if (parameter.Source == BindingSource.Form || parameter.Source == BindingSource.FormFile)
                    form[parameter.Name] = Schema(parameter.Type);
                else if (parameter.Source == BindingSource.Path || parameter.Source == BindingSource.Query)
                {
                    var schema = (Dictionary<string, object>)Schema(parameter.Type);
                    if (parameter.DefaultValue is not null) schema["default"] = parameter.DefaultValue;
                    parameters.Add(new { name = parameter.Name, @in = parameter.Source == BindingSource.Path ? "path" : "query",
                        required = parameter.Source == BindingSource.Path || parameter.IsRequired, schema });
                }
            }
            var fileParameters = api.ActionDescriptor.Parameters.Where(p => typeof(IFormFile).IsAssignableFrom(p.ParameterType)).ToList();
            if (fileParameters.Count > 0)
            {
                foreach(var key in form.Keys.Where(k=>k.StartsWith("file.",StringComparison.OrdinalIgnoreCase)||
                    new[]{"ContentType","ContentDisposition","Headers","Length","Name","FileName"}.Contains(k,StringComparer.OrdinalIgnoreCase)).ToList())form.Remove(key);
                foreach (var file in fileParameters) form[file.Name] = Schema(typeof(IFormFile));
            }
            if (form.Count > 0) operation["requestBody"] = new
            {
                required = true, content = new Dictionary<string, object>
                {
                    ["multipart/form-data"] = new { schema = new { type = "object", properties = form, required = form.Keys.ToArray() } }
                }
            };
            operation["parameters"] = parameters;
            var responses = new Dictionary<string, object>();
            foreach (var response in api.SupportedResponseTypes)
            {
                var bodyType = response.Type;
                var description = new Dictionary<string, object> { ["description"] = "HTTP " + response.StatusCode };
                if (bodyType is not null && bodyType != typeof(void))
                    description["content"] = new Dictionary<string, object> { ["application/json"] = new { schema = Schema(bodyType) } };
                responses[response.StatusCode.ToString()] = description;
            }
            if (responses.Count == 0) responses["200"] = new { description = "OK" };
            responses["default"] = new
            {
                description = "Ошибка. JSON ProblemDetails; 401 — сессия недействительна, 409 — конфликт установки/данных.",
                content = new Dictionary<string, object> { ["application/problem+json"] = new { schema = Schema(typeof(Microsoft.AspNetCore.Mvc.ProblemDetails)) } }
            };
            operation["responses"] = responses;
            if (!paths.TryGetValue(path, out var methods)) paths[path] = methods = new();
            methods[api.HttpMethod.ToLowerInvariant()] = operation;
        }
        return new
        {
            openapi = "3.0.3", info = new { title = "BackendFork API", version = "1.0.0",
                description = "Локальный API помощника инженера. PIN → Bearer-сессия. Фото и ресурс оцениваются отдельно." },
            paths, components = new
            {
                schemas, securitySchemes = new Dictionary<string, object>
                { ["pinSession"] = new { type = "http", scheme = "bearer", bearerFormat = "opaque session token" } }
            }
        };
    }
}
