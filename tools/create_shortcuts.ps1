param()

$ErrorActionPreference = 'Stop'
$repoRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot '..')).Path
$executable = Join-Path $repoRoot 'dist\ModelStudio\ModelStudio.exe'
$shortcutName = 'Модельная студия.lnk'

if (-not (Test-Path -LiteralPath $executable -PathType Leaf)) {
    throw "Сначала соберите приложение: не найден $executable"
}
$executable = (Resolve-Path -LiteralPath $executable).Path
$workingDirectory = [IO.Path]::GetDirectoryName($executable)
$desktop = [Environment]::GetFolderPath([Environment+SpecialFolder]::DesktopDirectory)
if ([string]::IsNullOrWhiteSpace($desktop) -or -not (Test-Path -LiteralPath $desktop -PathType Container)) {
    throw 'Не удалось определить существующую папку рабочего стола Windows.'
}

$shell = New-Object -ComObject WScript.Shell
$appUserModelId = 'ModelStudio.Desktop'
Add-Type -TypeDefinition @'
using System;
using System.Runtime.InteropServices;

[StructLayout(LayoutKind.Sequential)]
public struct ModelStudioPropertyKey
{
    public Guid FormatId;
    public uint PropertyId;
}

[StructLayout(LayoutKind.Sequential)]
public struct ModelStudioPropVariant
{
    public ushort Type;
    public ushort Reserved1;
    public ushort Reserved2;
    public ushort Reserved3;
    public IntPtr Value;
    public IntPtr Value2;
}

[ComImport, Guid("886D8EEB-8CF2-4446-8D02-CDBA1DBDCF99"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IModelStudioPropertyStore
{
    [PreserveSig] int GetCount(out uint count);
    [PreserveSig] int GetAt(uint index, out ModelStudioPropertyKey key);
    [PreserveSig] int GetValue(ref ModelStudioPropertyKey key, out ModelStudioPropVariant value);
    [PreserveSig] int SetValue(ref ModelStudioPropertyKey key, ref ModelStudioPropVariant value);
    [PreserveSig] int Commit();
}

[ComImport, Guid("0000010B-0000-0000-C000-000000000046"), InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
public interface IModelStudioPersistFile
{
    [PreserveSig] int GetClassID(out Guid classId);
    [PreserveSig] int IsDirty();
    [PreserveSig] int Load([MarshalAs(UnmanagedType.LPWStr)] string fileName, uint mode);
    [PreserveSig] int Save([MarshalAs(UnmanagedType.LPWStr)] string fileName, [MarshalAs(UnmanagedType.Bool)] bool remember);
    [PreserveSig] int SaveCompleted([MarshalAs(UnmanagedType.LPWStr)] string fileName);
    [PreserveSig] int GetCurFile(out IntPtr fileName);
}

public static class ModelStudioShortcutProperty
{
    private static readonly Guid ShellLinkClass = new Guid("00021401-0000-0000-C000-000000000046");
    private static ModelStudioPropertyKey AppUserModelIdKey = new ModelStudioPropertyKey {
        FormatId = new Guid("9F4C2855-9F79-4B39-A8D0-E1D42DE1D5F3"), PropertyId = 5
    };

    [DllImport("ole32.dll")]
    private static extern int PropVariantClear(ref ModelStudioPropVariant value);

    private static object Open(string path, out IModelStudioPersistFile persist, out IModelStudioPropertyStore store)
    {
        var comObject = Activator.CreateInstance(Type.GetTypeFromCLSID(ShellLinkClass, true));
        persist = (IModelStudioPersistFile)comObject;
        Marshal.ThrowExceptionForHR(persist.Load(path, 2));
        store = (IModelStudioPropertyStore)comObject;
        return comObject;
    }

    public static void Set(string path, string appUserModelId)
    {
        IModelStudioPersistFile persist = null;
        IModelStudioPropertyStore store = null;
        object comObject = null;
        var value = new ModelStudioPropVariant { Type = 31, Value = Marshal.StringToCoTaskMemUni(appUserModelId) };
        try {
            comObject = Open(path, out persist, out store);
            Marshal.ThrowExceptionForHR(store.SetValue(ref AppUserModelIdKey, ref value));
            Marshal.ThrowExceptionForHR(store.Commit());
            Marshal.ThrowExceptionForHR(persist.Save(path, true));
        }
        finally {
            PropVariantClear(ref value);
            if (comObject != null && Marshal.IsComObject(comObject)) Marshal.ReleaseComObject(comObject);
        }
    }

    public static string Get(string path)
    {
        IModelStudioPersistFile persist = null;
        IModelStudioPropertyStore store = null;
        object comObject = null;
        var value = new ModelStudioPropVariant();
        try {
            comObject = Open(path, out persist, out store);
            Marshal.ThrowExceptionForHR(store.GetValue(ref AppUserModelIdKey, out value));
            return value.Type == 31 && value.Value != IntPtr.Zero ? Marshal.PtrToStringUni(value.Value) : null;
        }
        finally {
            PropVariantClear(ref value);
            if (comObject != null && Marshal.IsComObject(comObject)) Marshal.ReleaseComObject(comObject);
        }
    }
}
'@
$destinations = @(
    (Join-Path $desktop $shortcutName),
    (Join-Path $repoRoot $shortcutName)
)

foreach ($shortcutPath in $destinations) {
    if (Test-Path -LiteralPath $shortcutPath -PathType Leaf) {
        $existing = $shell.CreateShortcut($shortcutPath)
        $existingTarget = if ($existing.TargetPath) { [IO.Path]::GetFullPath($existing.TargetPath) } else { '' }
        if (-not [string]::Equals($existingTarget, $executable, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Не перезаписываю существующий ярлык с другим назначением: $shortcutPath"
        }
    }

    $shortcut = $shell.CreateShortcut($shortcutPath)
    $shortcut.TargetPath = $executable
    $shortcut.WorkingDirectory = $workingDirectory
    $shortcut.IconLocation = "$executable,0"
    $shortcut.Description = 'Модельная студия'
    $shortcut.Save()
    [ModelStudioShortcutProperty]::Set($shortcutPath, $appUserModelId)

    $check = $shell.CreateShortcut($shortcutPath)
    if (-not [string]::Equals([IO.Path]::GetFullPath($check.TargetPath), $executable, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([IO.Path]::GetFullPath($check.WorkingDirectory), $workingDirectory, [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals($check.IconLocation, "$executable,0", [StringComparison]::OrdinalIgnoreCase) -or
        -not [string]::Equals([ModelStudioShortcutProperty]::Get($shortcutPath), $appUserModelId, [StringComparison]::Ordinal)) {
        throw "Не удалось проверить созданный ярлык: $shortcutPath"
    }
    [pscustomobject]@{
        Shortcut = $shortcutPath
        Target = $check.TargetPath
        WorkingDirectory = $check.WorkingDirectory
        IconLocation = $check.IconLocation
        AppUserModelID = [ModelStudioShortcutProperty]::Get($shortcutPath)
    }
}
