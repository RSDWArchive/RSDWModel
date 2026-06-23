using System.Reflection;
using System.Text.Json;
using CUE4Parse.Compression;
using CUE4Parse.FileProvider;
using CUE4Parse.FileProvider.Objects;
using CUE4Parse.MappingsProvider;
using CUE4Parse.UE4.Assets;
using CUE4Parse.UE4.Assets.Exports.Animation;
using CUE4Parse.UE4.Assets.Exports.Material;
using CUE4Parse.UE4.Assets.Exports.SkeletalMesh;
using CUE4Parse.UE4.Assets.Exports.StaticMesh;
using CUE4Parse.UE4.Assets.Exports.Texture;
using CUE4Parse.UE4.Objects.UObject;
using CUE4Parse.UE4.Versions;
using CUE4Parse_Conversion;
using CUE4Parse_Conversion.Animations;
using CUE4Parse_Conversion.Materials;
using CUE4Parse_Conversion.Meshes;
using CUE4Parse_Conversion.Textures;
using CUE4Parse_Conversion.Textures.BC;
using CUE4Parse_Conversion.UEFormat.Enums;
using Newtonsoft.Json;

namespace RsdwCueExtract;

internal static class Program
{
    private static readonly JsonSerializerOptions ManifestJsonOptions = new()
    {
        WriteIndented = true
    };

    public static int Main(string[] args)
    {
        try
        {
            var options = CliOptions.Parse(args);
            if (options.ShowHelp)
            {
                PrintHelp();
                return 0;
            }

            options.Validate();
            return Run(options);
        }
        catch (CliException ex)
        {
            Console.Error.WriteLine($"error: {ex.Message}");
            Console.Error.WriteLine("Run with --help for usage.");
            return 2;
        }
        catch (Exception ex)
        {
            Console.Error.WriteLine(ex);
            return 1;
        }
    }

    private static int Run(CliOptions options)
    {
        var retocRoot = Path.GetFullPath(options.RetocRoot!);
        var usmapPath = Path.GetFullPath(options.Usmap!);
        var outputRoot = options.Output is null ? null : Path.GetFullPath(options.Output);

        Console.WriteLine($"retoc root: {retocRoot}");
        Console.WriteLine($"usmap:      {usmapPath}");
        if (outputRoot is not null) Console.WriteLine($"output:     {outputRoot}");
        Console.WriteLine($"mode:       {(options.DryRun ? "dry-run" : "export")}");

        TryInitializeCompression();

        var version = new VersionContainer(EGame.GAME_UE5_6, ETexturePlatform.DesktopMobile);
        var provider = new DefaultFileProvider(retocRoot, SearchOption.AllDirectories, version)
        {
            MappingsContainer = new FileUsmapTypeMappingsProvider(usmapPath)
        };

        Console.WriteLine("initializing CUE4Parse provider...");
        provider.Initialize();
        provider.PostMount();

        var selectors = options.AssetSelectors
            .Select(selector => NormalizeAssetSelector(selector, retocRoot))
            .ToHashSet(StringComparer.OrdinalIgnoreCase);
        var names = options.NameSelectors
            .Select(StripAnyKnownExtension)
            .ToHashSet(StringComparer.OrdinalIgnoreCase);

        var files = provider.Files.Values
            .Where(IsPackageFile)
            .Where(file => MatchesSelection(file, selectors, names, options.Prefixes))
            .OrderBy(file => file.Path, StringComparer.OrdinalIgnoreCase)
            .ToList();

        var selectionLimit = options.Limit ?? (options.DryRun ? 20 : int.MaxValue);
        if (!options.All && selectionLimit != int.MaxValue)
        {
            files = files.Take(selectionLimit).ToList();
        }

        Console.WriteLine($"registered files: {provider.Files.Count:N0}");
        Console.WriteLine($"selected packages: {files.Count:N0}");

        if (files.Count == 0)
        {
            Console.WriteLine("No packages matched the requested selection.");
            return 0;
        }

        if (options.DryRun)
        {
            foreach (var file in files)
            {
                Console.WriteLine(file.Path);
            }

            return 0;
        }

        Directory.CreateDirectory(outputRoot!);
        var outputDirectory = new DirectoryInfo(outputRoot!);
        var results = new List<ExportResult>();
        var exportedMeshCount = 0;
        var exportedAnimationCount = 0;
        var failedCount = 0;
        var skippedCount = 0;
        var startedAt = DateTimeOffset.UtcNow;

        foreach (var (file, index) in files.Select((file, index) => (file, index + 1)))
        {
            Console.WriteLine($"[{index}/{files.Count}] {file.Path}");
            try
            {
                var expectedPath = options.ExportMeshes && !options.ExportAnimations
                    ? ExpectedMeshPath(outputRoot!, file.Path)
                    : options.ExportAnimations && !options.ExportMeshes
                        ? ExpectedAnimationPath(outputRoot!, file.Path)
                        : null;
                if (expectedPath is not null && !options.Force && File.Exists(expectedPath))
                {
                    skippedCount++;
                    results.Add(ExportResult.Skip(file.Path, expectedPath, options.ExportAnimations && !options.ExportMeshes));
                    Console.WriteLine($"  skipped existing: {Path.GetRelativePath(outputRoot!, expectedPath)}");
                    continue;
                }

                if (!provider.TryLoadPackage(file, out var package))
                {
                    failedCount++;
                    results.Add(ExportResult.Fail(file.Path, "package failed to load"));
                    Console.WriteLine("  failed: package failed to load");
                    continue;
                }

                var meshExports = options.ExportMeshes
                    ? ExportMeshesFromPackage(package, outputDirectory, options.ExportMaterials).ToList()
                    : [];
                var animationExports = options.ExportAnimations
                    ? ExportAnimationsFromPackage(package, outputDirectory).ToList()
                    : [];
                if (meshExports.Count == 0 && animationExports.Count == 0)
                {
                    failedCount++;
                    results.Add(ExportResult.Fail(file.Path, "no supported exports found"));
                    Console.WriteLine("  failed: no supported exports found");
                    continue;
                }

                exportedMeshCount += meshExports.Count;
                exportedAnimationCount += animationExports.Count;
                results.Add(ExportResult.CreateSuccess(file.Path, meshExports, animationExports));
                foreach (var exported in meshExports)
                {
                    Console.WriteLine($"  wrote: {Path.GetRelativePath(outputRoot!, exported.MeshPath)}");
                    if (exported.MaterialJsonCount > 0 || exported.TextureCount.GetValueOrDefault() > 0)
                    {
                        var textureSummary = exported.TextureCount?.ToString() ?? "not-tracked";
                        Console.WriteLine($"         materials: {exported.MaterialJsonCount}, textures: {textureSummary}");
                    }
                }
                foreach (var exported in animationExports)
                {
                    Console.WriteLine($"  wrote: {Path.GetRelativePath(outputRoot!, exported.AnimationPath)}");
                    Console.WriteLine($"         skeleton: {exported.SkeletonPath ?? "unknown"}");
                }
            }
            catch (Exception ex)
            {
                failedCount++;
                results.Add(ExportResult.Fail(file.Path, ex.Message));
                Console.WriteLine($"  failed: {ex.Message}");
            }
        }

        var manifestPath = options.Manifest ?? Path.Combine(outputRoot!, "CueExtractManifest.json");
        var manifest = new ExportManifest
        {
            StartedAtUtc = startedAt,
            FinishedAtUtc = DateTimeOffset.UtcNow,
            RetocRoot = retocRoot,
            Usmap = usmapPath,
            Output = outputRoot!,
            SelectedPackageCount = files.Count,
            ExportedMeshCount = exportedMeshCount,
            ExportedAnimationCount = exportedAnimationCount,
            ExportedFileCount = exportedMeshCount + exportedAnimationCount,
            FailedPackageCount = failedCount,
            SkippedPackageCount = skippedCount,
            Results = results
        };

        File.WriteAllText(manifestPath, System.Text.Json.JsonSerializer.Serialize(manifest, ManifestJsonOptions));
        Console.WriteLine($"manifest: {manifestPath}");
        Console.WriteLine($"done: exported {exportedMeshCount + exportedAnimationCount:N0} file(s) ({exportedMeshCount:N0} mesh, {exportedAnimationCount:N0} animation), skipped {skippedCount:N0}, failed {failedCount:N0} package(s)");
        return failedCount == 0 ? 0 : 1;
    }

    private static IEnumerable<MeshExport> ExportMeshesFromPackage(IPackage package, DirectoryInfo outputDirectory, bool exportMaterials)
    {
        var options = new ExporterOptions
        {
            LodFormat = ELodFormat.FirstLod,
            MeshFormat = EMeshFormat.UEFormat,
            AnimFormat = EAnimFormat.UEFormat,
            MaterialFormat = EMaterialFormat.AllLayersNoRef,
            TextureFormat = ETextureFormat.Png,
            CompressionFormat = EFileCompressionFormat.None,
            Platform = ETexturePlatform.DesktopMobile,
            SocketFormat = ESocketFormat.Bone,
            ExportMorphTargets = true,
            ExportMaterials = exportMaterials
        };

        foreach (var export in package.GetExports())
        {
            MeshExporter meshExporter = export switch
            {
                UStaticMesh staticMesh => new MeshExporter(staticMesh, options),
                USkeletalMesh skeletalMesh => new MeshExporter(skeletalMesh, options),
                _ => null!
            };

            if (meshExporter is null) continue;

            foreach (var mesh in meshExporter.MeshLods)
            {
                var materialJsonPaths = new List<string>();

                if (exportMaterials)
                {
                    var materials = mesh.Materials.Count > 0
                        ? mesh.Materials
                        : GetFallbackMaterialsFromMesh(export, options);

                    foreach (var material in materials)
                    {
                        var materialPath = WriteMaterialAndTextures(outputDirectory, material, options);
                        if (materialPath is not null)
                        {
                            materialJsonPaths.Add(materialPath);
                        }
                    }
                }

                var meshPath = CombineUnderRoot(outputDirectory.FullName, mesh.FileName);
                Directory.CreateDirectory(Path.GetDirectoryName(meshPath)!);
                File.WriteAllBytes(meshPath, mesh.FileData);

                yield return new MeshExport
                {
                    ExportName = export.Name,
                    ExportType = export.GetType().Name,
                    MeshPath = meshPath,
                    MaterialJsonCount = materialJsonPaths.Count,
                    TextureCount = null
                };
            }
        }
    }

    private static IEnumerable<AnimationExport> ExportAnimationsFromPackage(IPackage package, DirectoryInfo outputDirectory)
    {
        var options = new ExporterOptions
        {
            AnimFormat = EAnimFormat.UEFormat,
            CompressionFormat = EFileCompressionFormat.None,
            Platform = ETexturePlatform.DesktopMobile
        };

        foreach (var export in package.GetExports())
        {
            AnimExporter animExporter = export switch
            {
                UAnimSequence animSequence => new AnimExporter(animSequence, options),
                UAnimMontage animMontage => new AnimExporter(animMontage, options),
                UAnimComposite animComposite => new AnimExporter(animComposite, options),
                _ => null!
            };

            if (animExporter is null) continue;

            foreach (var anim in animExporter.AnimSequences)
            {
                var animationPath = CombineUnderRoot(outputDirectory.FullName, anim.FileName);
                Directory.CreateDirectory(Path.GetDirectoryName(animationPath)!);
                File.WriteAllBytes(animationPath, anim.FileData);

                yield return new AnimationExport
                {
                    ExportName = export.Name,
                    ExportType = export.GetType().Name,
                    AnimationPath = animationPath,
                    SkeletonPath = GetAnimationSkeletonPath(export)
                };
            }
        }
    }

    private static string? GetAnimationSkeletonPath(object export) => export switch
    {
        UAnimSequence animSequence => TryGetSkeletonPath(animSequence.Skeleton),
        UAnimMontage animMontage => TryGetSkeletonPath(animMontage.Skeleton),
        UAnimComposite animComposite => TryGetSkeletonPath(animComposite.Skeleton),
        _ => null
    };

    private static string? TryGetSkeletonPath(FPackageIndex? skeletonRef)
    {
        try
        {
            return skeletonRef is not null && skeletonRef.TryLoad<USkeleton>(out var skeleton)
                ? skeleton.GetPathName()
                : null;
        }
        catch
        {
            return null;
        }
    }

    private static List<MaterialExporter2> GetFallbackMaterialsFromMesh(object export, ExporterOptions options)
    {
        IEnumerable<ResolvedObject?> materialRefs = export switch
        {
            UStaticMesh staticMesh => staticMesh.Materials ?? [],
            USkeletalMesh skeletalMesh => skeletalMesh.Materials ?? [],
            _ => []
        };

        var outMaterials = new List<MaterialExporter2>();
        var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
        foreach (var materialRef in materialRefs)
        {
            if (materialRef is null)
            {
                continue;
            }

            var key = materialRef.GetPathName();
            if (!seen.Add(key))
            {
                continue;
            }

            try
            {
                if (materialRef.Load<UMaterialInterface>() is { } material)
                {
                    outMaterials.Add(new MaterialExporter2(material, options));
                }
            }
            catch
            {
                // Some material refs are editor-only or otherwise unloadable in cooked data.
            }
        }
        return outMaterials;
    }

    private static void TryInitializeCompression()
    {
        var detexPath = Path.Combine(AppContext.BaseDirectory, DetexHelper.DLL_NAME);
        if (!File.Exists(detexPath))
        {
            DetexHelper.LoadDll(detexPath);
        }

        if (File.Exists(detexPath))
        {
            DetexHelper.Initialize(detexPath);
        }

        foreach (var directory in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            var oodlePath = Path.Combine(directory, "oo2core_9_win64.dll");
            if (File.Exists(oodlePath))
            {
                OodleHelper.Initialize(oodlePath);
                break;
            }
        }

        foreach (var directory in new[] { AppContext.BaseDirectory, Environment.CurrentDirectory })
        {
            var zlibPath = Path.Combine(directory, "zlib-ng2.dll");
            if (File.Exists(zlibPath))
            {
                ZlibHelper.Initialize(zlibPath);
                break;
            }
        }
    }

    private static string? WriteMaterialAndTextures(DirectoryInfo outputDirectory, MaterialExporter2 material, ExporterOptions options)
    {
        try
        {
            var internalPath = ReadPrivateField<string>(material, "_internalFilePath");
            var materialData = ReadPrivateField<MaterialData>(material, "_materialData");

            if (string.IsNullOrWhiteSpace(internalPath))
            {
                return null;
            }

            var jsonPath = CombineUnderRoot(outputDirectory.FullName, internalPath + ".json");
            Directory.CreateDirectory(Path.GetDirectoryName(jsonPath)!);
            WriteAllTextWithRetry(jsonPath, JsonConvert.SerializeObject(materialData, Formatting.Indented));

            ExportMissingTextures(outputDirectory, materialData, options);

            return jsonPath;
        }
        catch (Exception ex)
        {
            Console.WriteLine($"         material warning: {ex.Message}");
            return null;
        }
    }

    private static void ExportMissingTextures(DirectoryInfo outputDirectory, MaterialData materialData, ExporterOptions options)
    {
        foreach (var texture in materialData.Parameters.Textures.Values)
        {
            if (texture is not UTexture2D texture2D)
            {
                continue;
            }

            var textureInternalPath = StripObjectSuffix(texture2D.Owner?.Provider?.FixPath(texture2D.Owner.Name) ?? texture2D.Name);
            if (string.IsNullOrWhiteSpace(textureInternalPath))
            {
                continue;
            }

            var likelyPngPath = CombineUnderRoot(outputDirectory.FullName, textureInternalPath + ".png");
            var likelyHdrPath = CombineUnderRoot(outputDirectory.FullName, textureInternalPath + ".hdr");
            if (File.Exists(likelyPngPath) || File.Exists(likelyHdrPath))
            {
                continue;
            }

            try
            {
                if (texture2D.Decode(options.Platform) is not { } bitmap)
                {
                    continue;
                }

                var imageData = bitmap.Encode(options.TextureFormat, options.ExportHdrTexturesAsHdr, out var ext);
                var texturePath = CombineUnderRoot(outputDirectory.FullName, textureInternalPath + "." + ext);
                Directory.CreateDirectory(Path.GetDirectoryName(texturePath)!);
                File.WriteAllBytes(texturePath, imageData);
            }
            catch (Exception ex)
            {
                Console.WriteLine($"         texture warning: {texture2D.Name}: {ex.Message}");
            }
        }
    }

    private static T? ReadPrivateField<T>(object instance, string fieldName)
    {
        var field = instance.GetType().GetField(fieldName, BindingFlags.Instance | BindingFlags.NonPublic);
        return field is null ? default : (T?) field.GetValue(instance);
    }

    private static void WriteAllTextWithRetry(string path, string text)
    {
        for (var attempt = 1; attempt <= 5; attempt++)
        {
            try
            {
                File.WriteAllText(path, text);
                return;
            }
            catch (IOException) when (attempt < 5)
            {
                Thread.Sleep(50 * attempt);
            }
        }
    }

    private static bool MatchesSelection(
        GameFile file,
        HashSet<string> assetSelectors,
        HashSet<string> nameSelectors,
        IReadOnlyList<string> prefixes)
    {
        var packagePath = StripPackageExtension(NormalizeSeparators(file.Path));
        var assetName = GetAssetName(packagePath);

        if (assetSelectors.Count > 0)
        {
            return assetSelectors.Contains(packagePath);
        }

        if (nameSelectors.Count > 0)
        {
            return nameSelectors.Contains(assetName);
        }

        return prefixes.Count == 0 || prefixes.Any(prefix => assetName.StartsWith(prefix, StringComparison.OrdinalIgnoreCase));
    }

    private static string ExpectedMeshPath(string outputRoot, string packagePath)
    {
        var relative = StripPackageExtension(NormalizeSeparators(packagePath)).TrimStart('/') + ".uemodel";
        return CombineUnderRoot(outputRoot, relative);
    }

    private static string ExpectedAnimationPath(string outputRoot, string packagePath)
    {
        var relative = StripPackageExtension(NormalizeSeparators(packagePath)).TrimStart('/') + ".ueanim";
        return CombineUnderRoot(outputRoot, relative);
    }

    private static bool IsPackageFile(GameFile file)
    {
        var path = NormalizeSeparators(file.Path);
        return path.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) ||
               path.EndsWith(".umap", StringComparison.OrdinalIgnoreCase);
    }

    private static string NormalizeAssetSelector(string selector, string retocRoot)
    {
        var normalized = NormalizeSeparators(selector.Trim().Trim('"'));

        if (Path.IsPathFullyQualified(normalized))
        {
            var full = Path.GetFullPath(normalized);
            var rel = Path.GetRelativePath(retocRoot, full);
            if (!rel.StartsWith("..", StringComparison.Ordinal))
            {
                normalized = NormalizeSeparators(rel);
            }
        }

        normalized = normalized.TrimStart('/');
        normalized = StripPackageExtension(normalized);

        if (normalized.StartsWith("Game/", StringComparison.OrdinalIgnoreCase))
        {
            return "RSDragonwilds/Content/" + normalized["Game/".Length..];
        }

        if (normalized.StartsWith("Engine/", StringComparison.OrdinalIgnoreCase) &&
            !normalized.StartsWith("Engine/Content/", StringComparison.OrdinalIgnoreCase))
        {
            return "Engine/Content/" + normalized["Engine/".Length..];
        }

        return normalized;
    }

    private static string StripPackageExtension(string path)
    {
        var stripped = StripAnyKnownExtension(path);
        return stripped.EndsWith(".uasset", StringComparison.OrdinalIgnoreCase) ||
               stripped.EndsWith(".umap", StringComparison.OrdinalIgnoreCase)
            ? Path.ChangeExtension(stripped, null) ?? stripped
            : stripped;
    }

    private static string StripAnyKnownExtension(string value)
    {
        foreach (var ext in new[] { ".uasset", ".umap", ".uemodel", ".json", ".png" })
        {
            if (value.EndsWith(ext, StringComparison.OrdinalIgnoreCase))
            {
                return value[..^ext.Length];
            }
        }

        return value;
    }

    private static string GetAssetName(string packagePath)
    {
        var normalized = NormalizeSeparators(packagePath);
        var slash = normalized.LastIndexOf('/');
        return slash >= 0 ? normalized[(slash + 1)..] : normalized;
    }

    private static string StripObjectSuffix(string path)
    {
        var dot = path.LastIndexOf('.');
        return dot >= 0 ? path[..dot] : path;
    }

    private static string CombineUnderRoot(string root, string relativePath)
    {
        var localRelative = NormalizeSeparators(relativePath).TrimStart('/').Replace('/', Path.DirectorySeparatorChar);
        var rootFull = Path.GetFullPath(root);
        var combined = Path.GetFullPath(Path.Combine(rootFull, localRelative));

        if (!combined.StartsWith(rootFull.TrimEnd(Path.DirectorySeparatorChar) + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase) &&
            !string.Equals(combined, rootFull, StringComparison.OrdinalIgnoreCase))
        {
            throw new InvalidOperationException($"refusing to write outside output root: {relativePath}");
        }

        return combined;
    }

    private static string NormalizeSeparators(string value) => value.Replace('\\', '/');

    private static void PrintHelp()
    {
        Console.WriteLine("""
RSDW CUE4Parse extractor spike

Usage:
  dotnet run --project tools/CueExtract/RsdwCueExtract -- [options]

Required:
  --retoc-root <path>   Root of retoc to-legacy output containing RSDragonwilds and Engine.
  --usmap <path>        Matching RSDragonwilds .usmap file.
  --out <path>          Export output root. Required unless --dry-run is used.

Selection:
  --asset <path>        Exact package path. Repeatable. /Game/... maps to RSDragonwilds/Content/...
  --name <asset>        Exact asset name, e.g. SM_Campfire_01. Repeatable.
  --prefix <prefixes>   Comma-separated asset prefixes. Default: SM_,SK_.
  --limit <n>           Maximum selected packages.
  --all                 Export all matches. Required for broad exports without --limit.

Mode:
  --dry-run             Print selected package paths without exporting.
  --force               Re-export meshes even when the output .uemodel already exists.
  --animations          Export .ueanim animation files in addition to meshes.
  --animations-only     Export .ueanim animation files without mesh/material output.
  --no-materials        Export .uemodel files only.
  --manifest <path>     Manifest path. Default: <out>/CueExtractManifest.json.
  --help                Show this help.

Examples:
  dotnet run --project tools/CueExtract/RsdwCueExtract -- --dry-run --retoc-root E:\Github\Retoc\RSDragonwilds\0.12.0.0 --usmap E:\Github\Retoc\RSDragonwilds\0.12.0.0\RSDragonwilds-5.6.1-203193+++dominion+staging-0196ef29.usmap --name SM_Campfire_01

  dotnet run --project tools/CueExtract/RsdwCueExtract -- --retoc-root E:\Github\Retoc\RSDragonwilds\0.12.0.0 --usmap E:\Github\Retoc\RSDragonwilds\0.12.0.0\RSDragonwilds-5.6.1-203193+++dominion+staging-0196ef29.usmap --out E:\Github\RSDWModel\0.12.0.0 --all
""");
    }
}

internal sealed class CliOptions
{
    public string? RetocRoot { get; private set; }
    public string? Usmap { get; private set; }
    public string? Output { get; private set; }
    public string? Manifest { get; private set; }
    public bool DryRun { get; private set; }
    public bool All { get; private set; }
    public bool Force { get; private set; }
    public bool ShowHelp { get; private set; }
    public bool ExportMaterials { get; private set; } = true;
    public bool ExportMeshes { get; private set; } = true;
    public bool ExportAnimations { get; private set; }
    public int? Limit { get; private set; }
    public List<string> AssetSelectors { get; } = [];
    public List<string> NameSelectors { get; } = [];
    public List<string> Prefixes { get; } = ["SM_", "SK_"];

    public static CliOptions Parse(string[] args)
    {
        var options = new CliOptions();

        for (var i = 0; i < args.Length; i++)
        {
            var arg = args[i];
            switch (arg)
            {
                case "--help":
                case "-h":
                    options.ShowHelp = true;
                    break;
                case "--retoc-root":
                    options.RetocRoot = RequireValue(args, ref i, arg);
                    break;
                case "--usmap":
                    options.Usmap = RequireValue(args, ref i, arg);
                    break;
                case "--out":
                    options.Output = RequireValue(args, ref i, arg);
                    break;
                case "--manifest":
                    options.Manifest = RequireValue(args, ref i, arg);
                    break;
                case "--dry-run":
                    options.DryRun = true;
                    break;
                case "--all":
                    options.All = true;
                    break;
                case "--force":
                    options.Force = true;
                    break;
                case "--animations":
                    options.ExportAnimations = true;
                    break;
                case "--animations-only":
                    options.ExportMeshes = false;
                    options.ExportAnimations = true;
                    options.ExportMaterials = false;
                    break;
                case "--no-materials":
                    options.ExportMaterials = false;
                    break;
                case "--asset":
                    options.AssetSelectors.AddRange(SplitCsv(RequireValue(args, ref i, arg)));
                    break;
                case "--name":
                    options.NameSelectors.AddRange(SplitCsv(RequireValue(args, ref i, arg)));
                    break;
                case "--prefix":
                    options.Prefixes.Clear();
                    options.Prefixes.AddRange(SplitCsv(RequireValue(args, ref i, arg)));
                    break;
                case "--limit":
                    var rawLimit = RequireValue(args, ref i, arg);
                    if (!int.TryParse(rawLimit, out var limit) || limit < 1)
                    {
                        throw new CliException("--limit must be a positive integer");
                    }
                    options.Limit = limit;
                    break;
                default:
                    throw new CliException($"unknown option '{arg}'");
            }
        }

        return options;
    }

    public void Validate()
    {
        if (ShowHelp) return;

        if (string.IsNullOrWhiteSpace(RetocRoot)) throw new CliException("--retoc-root is required");
        if (!Directory.Exists(RetocRoot)) throw new CliException($"--retoc-root does not exist: {RetocRoot}");
        if (string.IsNullOrWhiteSpace(Usmap)) throw new CliException("--usmap is required");
        if (!File.Exists(Usmap)) throw new CliException($"--usmap does not exist: {Usmap}");

        if (!DryRun && string.IsNullOrWhiteSpace(Output))
        {
            throw new CliException("--out is required unless --dry-run is used");
        }

        if (!DryRun && !All && Limit is null && AssetSelectors.Count == 0 && NameSelectors.Count == 0)
        {
            throw new CliException("broad export requires --limit, --asset, --name, or --all");
        }

        if (!ExportMeshes && !ExportAnimations)
        {
            throw new CliException("nothing to export");
        }
    }

    private static string RequireValue(string[] args, ref int index, string optionName)
    {
        if (index + 1 >= args.Length || args[index + 1].StartsWith("--", StringComparison.Ordinal))
        {
            throw new CliException($"{optionName} requires a value");
        }

        index++;
        return args[index];
    }

    private static IEnumerable<string> SplitCsv(string value)
    {
        return value
            .Split(',', StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
            .Where(item => item.Length > 0);
    }
}

internal sealed class CliException(string message) : Exception(message);

internal sealed class MeshExport
{
    public required string ExportName { get; init; }
    public required string ExportType { get; init; }
    public required string MeshPath { get; init; }
    public int MaterialJsonCount { get; init; }
    public int? TextureCount { get; init; }
}

internal sealed class AnimationExport
{
    public required string ExportName { get; init; }
    public required string ExportType { get; init; }
    public required string AnimationPath { get; init; }
    public string? SkeletonPath { get; init; }
}

internal sealed class ExportResult
{
    public required string PackagePath { get; init; }
    public required bool Succeeded { get; init; }
    public string? Error { get; init; }
    public List<MeshExport> Meshes { get; init; } = [];
    public List<AnimationExport> Animations { get; init; } = [];

    public static ExportResult CreateSuccess(string packagePath, List<MeshExport> meshes, List<AnimationExport> animations) => new()
    {
        PackagePath = packagePath,
        Succeeded = true,
        Meshes = meshes,
        Animations = animations
    };

    public static ExportResult Fail(string packagePath, string error) => new()
    {
        PackagePath = packagePath,
        Succeeded = false,
        Error = error
    };

    public static ExportResult Skip(string packagePath, string outputPath, bool isAnimation) => new()
    {
        PackagePath = packagePath,
        Succeeded = true,
        Error = "skipped existing output",
        Meshes = isAnimation
            ? []
            :
            [
                new MeshExport
                {
                    ExportName = Path.GetFileNameWithoutExtension(outputPath),
                    ExportType = "Existing",
                    MeshPath = outputPath,
                    MaterialJsonCount = 0,
                    TextureCount = 0
                }
            ],
        Animations = isAnimation
            ?
            [
                new AnimationExport
                {
                    ExportName = Path.GetFileNameWithoutExtension(outputPath),
                    ExportType = "Existing",
                    AnimationPath = outputPath,
                    SkeletonPath = null
                }
            ]
            : []
    };
}

internal sealed class ExportManifest
{
    public DateTimeOffset StartedAtUtc { get; init; }
    public DateTimeOffset FinishedAtUtc { get; init; }
    public required string RetocRoot { get; init; }
    public required string Usmap { get; init; }
    public required string Output { get; init; }
    public int SelectedPackageCount { get; init; }
    public int ExportedMeshCount { get; init; }
    public int ExportedAnimationCount { get; init; }
    public int ExportedFileCount { get; init; }
    public int FailedPackageCount { get; init; }
    public int SkippedPackageCount { get; init; }
    public List<ExportResult> Results { get; init; } = [];
}
