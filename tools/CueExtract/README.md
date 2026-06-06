# RSDW CUE4Parse Extractor

This is the CUE4Parse extraction step for the RSDWModel Python/Blender pipeline.

For normal game updates, prefer the repository-level orchestrator:

```powershell
python tools\UpdateGameData.py
```

Use the commands below when debugging or running only the extraction step.

## Current Flow

1. Generate the current `.usmap` and UHT header dump with UE4SS. The `.usmap`
   is expected under the game's `Binaries\Win64\ue4ss` folder.
2. Convert the game's `Content\Paks` folder with `retoc`:

   ```powershell
   retoc to-legacy `
     F:\SteamLibrary\steamapps\common\RSDragonwilds\RSDragonwilds\Content\Paks `
     E:\Github\Retoc\RSDragonwilds\0.11.2.2
   ```

3. Copy the `.usmap` into the versioned retoc cache, then build and run the
   extractor against the retoc output:

   ```powershell
   dotnet run --project tools\CueExtract\RsdwCueExtract `
     /p:Cue4ParseRoot=E:\Github\CUE4Parse `
     -- `
     --retoc-root E:\Github\Retoc\RSDragonwilds\0.11.2.2 `
     --usmap E:\Github\Retoc\RSDragonwilds\0.11.2.2\RSDragonwilds-5.6.1-203193+++dominion+staging-0196ef29.usmap `
     --out E:\Github\RSDWModel\0.11.2.2 `
     --all
   ```

For the current RSDW `.usmap` format, use a current CUE4Parse source checkout
through `Cue4ParseRoot` or the `CUE4PARSE_ROOT` environment variable. The latest
NuGet package can build the tool, but does not parse the current usmap version.

## Verified Status

The current 0.11.2.2 run was verified with:

- `4,894` `.uemodel` files exported into `E:\Github\RSDWModel\0.11.2.2`.
- `4,394` `SM_` and `500` `SK_` inventory entries compiled.
- One `SM_` and one `SK_` model converted successfully through the Blender GLB
  worker.
