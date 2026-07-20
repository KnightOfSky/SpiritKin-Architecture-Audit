using System;
using System.Linq;
using System.Windows;
using Microsoft.Win32;

namespace SpiritKinDesktop;

/// <summary>
/// 日/夜双主题热切换：运行时替换 App 级 MergedDictionaries 第 0 项（主题字典）。
/// 颜色引用一律 DynamicResource，替换后自动刷新。跟随系统读注册表 AppsUseLightTheme。
/// 本类仅做主题接线，不改任何既有业务逻辑（符合 multi_client_art_plan.md 禁改区约束）。
/// </summary>
internal static class ThemeManager
{
    private static bool _systemEventsSubscribed;
    internal enum ThemeMode
    {
        System,
        Light,
        Dark,
    }

    internal const string ThemeModeSettingKey = "appearance.theme_mode.atelier";

    // 本地快速缓存：主题偏好落注册表，App.OnStartup 可同步读取，窗口首帧即以正确主题打开。
    // 共享状态（State.Settings）走后端 HTTP 往返，要等本地服务起来才到，只用它会先渲染兜底主题再热切一次（视觉闪一下）。
    private const string CacheRegistryPath = @"Software\SpiritKin\Desktop";
    private const string CacheRegistryValue = "theme_mode_atelier";

    private const string LightDictionarySource = "Resources/Themes/Fantasy.Light.xaml";
    private const string DarkDictionarySource = "Resources/Themes/Fantasy.Dark.xaml";

    /// <summary>当前实际生效的是否为夜主题（供 WebView2 联动、状态查询用）。</summary>
    internal static bool CurrentIsDark { get; private set; }

    /// <summary>当前主题模式（跟随系统/日间/夜间）。</summary>
    internal static ThemeMode CurrentMode { get; private set; } = ThemeMode.Dark;

    /// <summary>主题实际切换后触发，参数为是否夜主题；WebView2 内嵌页据此换肤。</summary>
    internal static event Action<bool>? ThemeChanged;

    internal static ThemeMode ParseMode(string? raw) => raw?.Trim().ToLowerInvariant() switch
    {
        "light" => ThemeMode.Light,
        "dark" => ThemeMode.Dark,
        _ => ThemeMode.System,
    };

    internal static string SerializeMode(ThemeMode mode) => mode switch
    {
        ThemeMode.Light => "light",
        ThemeMode.Dark => "dark",
        _ => "system",
    };

    /// <summary>应用主题模式，热替换主题字典。返回实际生效的是否夜主题。</summary>
    internal static bool ApplyMode(ThemeMode mode)
    {
        CurrentMode = mode;
        if (mode == ThemeMode.System)
        {
            SubscribeToSystemTheme();
        }
        else
        {
            UnsubscribeFromSystemTheme();
        }
        CacheMode(mode);
        var isDark = ResolveEffectiveIsDark(mode);
        SwapThemeDictionary(isDark);
        return isDark;
    }

    /// <summary>从本地注册表快速读取上次的主题模式（默认跟随系统）。App.OnStartup 同步调用，无网络往返。</summary>
    internal static ThemeMode LoadCachedMode()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(CacheRegistryPath);
            if (key?.GetValue(CacheRegistryValue) is string raw)
            {
                return ParseMode(raw);
            }
        }
        catch
        {
            // 注册表读取失败（策略限制等）时回退跟随系统，不影响启动。
        }
        return ThemeMode.Dark;
    }

    private static void CacheMode(ThemeMode mode)
    {
        try
        {
            using var key = Registry.CurrentUser.CreateSubKey(CacheRegistryPath);
            key?.SetValue(CacheRegistryValue, SerializeMode(mode), RegistryValueKind.String);
        }
        catch
        {
            // 写缓存失败不致命：下次启动退回共享状态回放路径（慢一拍但仍正确）。
        }
    }

    internal static void Shutdown() => UnsubscribeFromSystemTheme();

    private static void SubscribeToSystemTheme()
    {
        if (_systemEventsSubscribed)
        {
            return;
        }
        SystemEvents.UserPreferenceChanged += SystemEvents_UserPreferenceChanged;
        _systemEventsSubscribed = true;
    }

    private static void UnsubscribeFromSystemTheme()
    {
        if (!_systemEventsSubscribed)
        {
            return;
        }
        SystemEvents.UserPreferenceChanged -= SystemEvents_UserPreferenceChanged;
        _systemEventsSubscribed = false;
    }

    private static void SystemEvents_UserPreferenceChanged(object sender, UserPreferenceChangedEventArgs e)
    {
        if (CurrentMode != ThemeMode.System || e.Category is UserPreferenceCategory.Locale or UserPreferenceCategory.Keyboard)
        {
            return;
        }
        var app = Application.Current;
        if (app is null)
        {
            return;
        }
        _ = app.Dispatcher.InvokeAsync(() =>
        {
            if (CurrentMode != ThemeMode.System)
            {
                return;
            }
            var isDark = ResolveEffectiveIsDark(ThemeMode.System);
            if (isDark != CurrentIsDark)
            {
                SwapThemeDictionary(isDark);
            }
        });
    }

    private static bool ResolveEffectiveIsDark(ThemeMode mode) => mode switch
    {
        ThemeMode.Light => false,
        ThemeMode.Dark => true,
        _ => SystemPrefersDark(),
    };

    /// <summary>读注册表判断系统是否处于深色模式（AppsUseLightTheme=0 表示深色）。</summary>
    private static bool SystemPrefersDark()
    {
        try
        {
            using var key = Registry.CurrentUser.OpenSubKey(
                @"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize");
            if (key?.GetValue("AppsUseLightTheme") is int appsUseLight)
            {
                return appsUseLight == 0;
            }
        }
        catch
        {
            // 注册表读取失败（策略限制等）时回退浅色，不影响启动。
        }
        return false;
    }

    private static void SwapThemeDictionary(bool isDark)
    {
        var app = Application.Current;
        if (app is null)
        {
            return;
        }

        var source = isDark ? DarkDictionarySource : LightDictionarySource;
        var next = new ResourceDictionary { Source = new Uri(source, UriKind.Relative) };
        var merged = app.Resources.MergedDictionaries;

        var existing = merged.FirstOrDefault(dict =>
            dict.Source is { } uri &&
            (uri.OriginalString.EndsWith("Fantasy.Light.xaml", StringComparison.OrdinalIgnoreCase) ||
             uri.OriginalString.EndsWith("Fantasy.Dark.xaml", StringComparison.OrdinalIgnoreCase)));

        if (existing is not null)
        {
            var index = merged.IndexOf(existing);
            merged[index] = next;
        }
        else
        {
            // 主题字典约定置于第 0 项；找不到时兜底插到最前，保证样式能解析颜色键。
            merged.Insert(0, next);
        }

        CurrentIsDark = isDark;
        ThemeChanged?.Invoke(isDark);
    }
}
