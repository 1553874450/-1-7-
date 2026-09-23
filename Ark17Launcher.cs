using System;
using System.Diagnostics;
using System.IO;
using System.Text;
using System.Windows.Forms;

internal static class Ark17Launcher
{
    [STAThread]
    private static int Main(string[] args)
    {
        string baseDir = AppDomain.CurrentDomain.BaseDirectory;
        string exeName = Path.GetFileNameWithoutExtension(Application.ExecutablePath);
        string scriptName = SelectScriptName(exeName);
        string script = Path.Combine(baseDir, scriptName);

        if (!File.Exists(script))
        {
            MessageBox.Show(
                scriptName + " was not found next to this launcher.",
                "Arknights 1-7 Launcher",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }

        string arguments =
            "-NoExit -ExecutionPolicy Bypass -File " +
            Quote(script) +
            BuildExtraArgs(args);

        try
        {
            var startInfo = new ProcessStartInfo
            {
                FileName = "powershell.exe",
                Arguments = arguments,
                WorkingDirectory = baseDir,
                UseShellExecute = true,
            };

            Process.Start(startInfo);
            return 0;
        }
        catch (Exception ex)
        {
            MessageBox.Show(
                ex.Message,
                "Arknights 1-7 Launcher",
                MessageBoxButtons.OK,
                MessageBoxIcon.Error);
            return 1;
        }
    }

    private static string BuildExtraArgs(string[] args)
    {
        if (args == null || args.Length == 0)
        {
            return string.Empty;
        }

        var builder = new StringBuilder();
        foreach (string arg in args)
        {
            builder.Append(' ');
            builder.Append(Quote(arg));
        }

        return builder.ToString();
    }

    private static string SelectScriptName(string exeName)
    {
        if (exeName.Contains("游戏内"))
        {
            return "游戏内找1-7刷图.ps1";
        }

        if (exeName.Contains("设备桌面"))
        {
            return "从设备桌面启动并刷1-7.ps1";
        }

        return "run_ark_1_7.ps1";
    }

    private static string Quote(string value)
    {
        return "\"" + value.Replace("\"", "\\\"") + "\"";
    }
}
