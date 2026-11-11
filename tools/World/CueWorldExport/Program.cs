using System.Security.Cryptography;
using System.Text;
using System.Text.Json;
using CUE4Parse.FileProvider;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports;
using CUE4Parse.UE4.Assets.Exports.Actor;
using CUE4Parse.UE4.Assets.Exports.Component.Landscape;
using CUE4Parse.UE4.Assets.Exports.Component.SplineMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Landscape;
using CUE4Parse_Conversion.Meshes;
using CUE4Parse_Conversion.Textures;
using CUE4Parse_Conversion.Textures.BC;
using SkiaSharp;

if (args.Length != 4)
    throw new ArgumentException("Usage: CueWorldExport <retoc-root> <usmap> <requests.json> <out-root>");
var root = Path.GetFullPath(args[3]);
Directory.CreateDirectory(root);
var detex = Path.Combine(AppContext.BaseDirectory, DetexHelper.DLL_NAME);
if (!File.Exists(detex)) DetexHelper.LoadDll(detex);
DetexHelper.Initialize(detex);
using var provider = new DefaultFileProvider(args[0], SearchOption.AllDirectories,
    new VersionContainer(EGame.GAME_UE5_6, ETexturePlatform.DesktopMobile), StringComparer.OrdinalIgnoreCase)
{ MappingsContainer = new FileUsmapTypeMappingsProvider(args[1]) };
provider.Initialize();
provider.PostMount();
// Loose Retoc exports do not always contain the plugin manifests used for mounts.
foreach (var path in provider.Files.Keys)
{
    var plugin = path.IndexOf("/Plugins/", StringComparison.OrdinalIgnoreCase);
    var content = plugin < 0 ? -1 : path.IndexOf("/Content/", plugin, StringComparison.OrdinalIgnoreCase);
    if (content < 0) continue;
    var directory = path[..content];
    var mount = directory[(directory.LastIndexOf('/')+1)..];
    provider.VirtualPaths.TryAdd(mount, directory);
}
var packages = new Dictionary<string, IPackage>();
var requests = JsonDocument.Parse(File.ReadAllText(args[2])).RootElement;
var results = new List<object>();
var failed = 0;
var options = new ExporterOptions { MeshFormat = EMeshFormat.Gltf2, LodFormat = ELodFormat.FirstLod,
    ExportMaterials = false, TextureFormat = ETextureFormat.Png, Platform = ETexturePlatform.DesktopMobile };

UObject Resolve(string identity)
{
    var split = identity.LastIndexOf('.');
    var package = identity[..split];
    if (!packages.TryGetValue(package, out var loaded))
    {
        loaded = provider.LoadPackage(package);
        packages[package] = loaded;
    }
    return loaded.GetExport(int.Parse(identity[(split+1)..])) ?? throw new Exception($"Missing {identity}");
}

string Key(string identity) => Convert.ToHexString(SHA256.HashData(Encoding.UTF8.GetBytes(identity)))[..20].ToLowerInvariant();

foreach (var request in requests.EnumerateArray())
{
    var source = request.GetProperty("source").GetString()!;
    var kind = request.GetProperty("kind").GetString()!;
    var id = request.TryGetProperty("id", out var explicitId) ? explicitId.GetString()! : Key(source);
    if (id.Any(c => !char.IsAsciiLetterOrDigit(c) && c != '-')) throw new Exception("Unsafe output id");
    try
    {
        var obj = Resolve(source);
        if (request.TryGetProperty("name", out var name) && !string.Equals(obj.Name, name.GetString(), StringComparison.OrdinalIgnoreCase))
            throw new Exception($"Export identity mismatch: {source}: {obj.Name} != {name}");
        Console.WriteLine($"{kind}: {source} ({obj.Name})");
        if (kind == "texture_array")
        {
            var decoded = ((UTexture2DArray)obj).DecodeTextureArray() ?? throw new Exception("Array decode returned null");
            var files = new List<string>();
            for (int slice = 0; slice < decoded.Length; slice++)
            {
                var file = $"{id}-{slice}.png";
                File.WriteAllBytes(Path.Combine(root, file), decoded[slice].Encode(ETextureFormat.Png, false, out _));
                files.Add(file);
            }
            results.Add(new { source, kind, id, files, file = files[0], object_path = obj.GetPathName() });
        }
        else if (kind == "texture")
        {
            var texture = (UTexture2D)obj;
            var mip = texture.GetMip(0);
            var bitmap = texture.Decode(ETexturePlatform.DesktopMobile) ?? throw new Exception("Texture decode returned null");
            var file = id + ".png";
            File.WriteAllBytes(Path.Combine(root, file), bitmap.Encode(ETextureFormat.Png, false, out _));
            string? raw = null;
            if (texture.Format == EPixelFormat.PF_B8G8R8A8 && mip != null)
            {
                raw = id + ".bgra";
                File.WriteAllBytes(Path.Combine(root, raw), mip.BulkData?.Data ?? throw new Exception("Missing raw texture bytes"));
            }
            results.Add(new { source, kind, id, file, raw, width = bitmap.Width, height = bitmap.Height,
                format = texture.Format.ToString(), object_path = obj.GetPathName() });
        }
        else if (kind == "landscape_probe")
        {
            var component = (ULandscapeComponent)obj;
            var owner = (ALandscapeProxy)Resolve(request.GetProperty("owner").GetString()!);
            var exporter = new LandscapeExporter(owner, [component], options, ELandscapeExportFlags.All);
            var directory = new DirectoryInfo(Path.Combine(root, id));
            directory.Create();
            if (!exporter.TryWriteToDir(directory, out var label, out var saved)) throw new Exception("Landscape exporter produced no files");
            results.Add(new { source, kind, id, file = Path.GetRelativePath(root, saved).Replace('\\', '/') });
        }
        else
        {
            var spline = kind == "spline" ? (USplineMeshComponent)obj : null;
            var mesh = spline == null ? (UStaticMesh)obj : spline.GetLoadedStaticMesh()!;
            var exporter = new MeshExporter(mesh, spline, options);
            var exported = exporter.MeshLods.FirstOrDefault() ?? throw new Exception("No mesh LOD exported");
            var file = id + ".glb";
            File.WriteAllBytes(Path.Combine(root, file), exported.FileData);
            results.Add(new { source, kind, id, file, object_path = obj.GetPathName() });
        }
    }
    catch (Exception ex)
    {
        failed++;
        Console.Error.WriteLine(ex);
        results.Add(new { source, kind, id, error = ex.ToString() });
    }
}
File.WriteAllText(Path.Combine(root, "NativeExport.json"), JsonSerializer.Serialize(results, new JsonSerializerOptions { WriteIndented = true }));
return failed == 0 ? 0 : 1;
