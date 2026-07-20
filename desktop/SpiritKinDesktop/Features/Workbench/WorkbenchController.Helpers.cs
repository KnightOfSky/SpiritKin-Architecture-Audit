using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Text;
using System.Windows;
using System.Windows.Input;
using System.Windows.Media;

namespace SpiritKinDesktop;

internal sealed partial class WorkbenchController
{
    internal static string? ResolveCommandPath(string command)
    {
        try
        {
            var startInfo = new ProcessStartInfo("where.exe", command)
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
            };
            using var process = Process.Start(startInfo);
            if (process is null)
            {
                return null;
            }
            var output = process.StandardOutput.ReadToEnd();
            process.WaitForExit(1500);
            return output
                .Split(new[] { "\r\n", "\n", "\r" }, StringSplitOptions.RemoveEmptyEntries | StringSplitOptions.TrimEntries)
                .FirstOrDefault(File.Exists);
        }
        catch
        {
            return null;
        }
    }

    internal static string ResolveShellExecutable() => ResolveCommandPath("pwsh") ?? "powershell.exe";

    internal static string EncodePowerShellCommand(string command) => Convert.ToBase64String(Encoding.Unicode.GetBytes(command));

    internal static string QuotePowerShellLiteral(string value) => $"'{value.Replace("'", "''")}'";

    internal static string QuoteArg(string value) => $"\"{value.Replace("\"", "\\\"")}\"";

    internal static string ResolveGitWorkingDirectory(string activeWorkspace, string applicationRoot)
    {
        var candidate = string.IsNullOrWhiteSpace(activeWorkspace) ? applicationRoot : activeWorkspace;
        return Directory.Exists(candidate) ? Path.GetFullPath(candidate) : Path.GetFullPath(applicationRoot);
    }

    internal static bool IsTextInputKey(Key key)
    {
        if (key is Key.Space or Key.OemPlus or Key.OemComma or Key.OemMinus or Key.OemPeriod or Key.OemQuestion or Key.Oem1 or Key.Oem2 or Key.Oem3 or Key.Oem4 or Key.Oem5 or Key.Oem6 or Key.Oem7)
        {
            return true;
        }
        return (key >= Key.A && key <= Key.Z) || (key >= Key.D0 && key <= Key.D9) || (key >= Key.NumPad0 && key <= Key.NumPad9);
    }

    internal static bool IsWithin<T>(DependencyObject? source) where T : DependencyObject
    {
        while (source is not null)
        {
            if (source is T)
            {
                return true;
            }
            source = VisualTreeHelper.GetParent(source) ?? LogicalTreeHelper.GetParent(source);
        }
        return false;
    }
}
