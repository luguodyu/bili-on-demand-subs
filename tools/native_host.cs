// B站字幕服务 Native Messaging Host（Chrome/Edge 原生宿主启动器）
// 职责：扩展发 {"type":"start"} -> 本程序以隐藏窗口拉起同目录 engine\pythonw.exe subtitle-server.py；
//       {"type":"stop"} 停止；stdin 关闭（浏览器/后台 SW 退出）时自动杀掉服务子进程，不留孤儿。
// 编译：C:\Windows\Microsoft.NET\Framework64\v4.0.30319\csc.exe /nologo /out:subtitle-native.exe native_host.cs
// 协议：stdin/stdout 均为 4 字节小端长度 + UTF-8 JSON（Chrome Native Messaging 规范）。
using System;
using System.Diagnostics;
using System.IO;
using System.Text;

class SubtitleNativeHost
{
    static Process child = null;

    static string ExeDir()
    {
        return AppDomain.CurrentDomain.BaseDirectory;
    }

    static int ReadExact(Stream s, byte[] buf, int n)
    {
        int off = 0;
        while (off < n)
        {
            int r = s.Read(buf, off, n - off);
            if (r <= 0) return off;
            off += r;
        }
        return off;
    }

    static void WriteReply(string json)
    {
        byte[] body = Encoding.UTF8.GetBytes(json);
        byte[] head = BitConverter.GetBytes((uint)body.Length); // 小端
        Stream outp = Console.OpenStandardOutput();
        outp.Write(head, 0, 4);
        outp.Write(body, 0, body.Length);
        outp.Flush();
    }

    static void DebugLog(string line)
    {
        try
        {
            if (Environment.GetEnvironmentVariable("DSH_HOST_DEBUG") != "1") return;
            string p = Path.Combine(ExeDir(), "native-host-debug.log");
            File.AppendAllText(p, DateTime.Now.ToString("HH:mm:ss.fff") + " " + line + "\r\n");
        }
        catch { }
    }

    static bool SpawnServer()
    {
        if (child != null && !child.HasExited) return true; // 已在跑
        DebugLog("SpawnServer: no running child, spawn begin");
        string dir = ExeDir();
        string python = Path.Combine(dir, "engine", "python.exe"); // pythonw 在该启动方式下会卡死，统一用 python.exe
        string server = Path.Combine(dir, "subtitle-server.py");
        if (!File.Exists(python) || !File.Exists(server))
        {
            WriteReply("{\"ok\":false,\"error\":\"missing engine\\\\python.exe or subtitle-server.py\"}");
            return false;
        }
        try
        {
            ProcessStartInfo psi = new ProcessStartInfo();
            psi.FileName = python;
            psi.Arguments = "\"" + server + "\"";
            psi.WorkingDirectory = dir;
            psi.UseShellExecute = false;
            // 注意：不能用 CreateNoWindow(CREATE_NO_WINDOW) 拉起 python——实测会让其卡死在启动早期；
            // 用 SW_HIDE + 空 stdin + 输出重定向排空，等效“无窗口”且能正常启动。
            psi.CreateNoWindow = false;
            psi.WindowStyle = ProcessWindowStyle.Hidden;
            psi.RedirectStandardInput = true;
            psi.RedirectStandardOutput = true;
            psi.RedirectStandardError = true;
            child = Process.Start(psi);
            DebugLog("child started pid=" + child.Id);
            child.StandardInput.Close(); // 立即给子进程一个已关闭的空 stdin
            child.EnableRaisingEvents = true;
            child.Exited += (s2, e2) => { DebugLog("child exited code=" + child.ExitCode); };
            Drain(child.StandardOutput);
            Drain(child.StandardError);
            return true;
        }
        catch (Exception e)
        {
            WriteReply("{\"ok\":false,\"error\":\"" + JsonEsc(e.Message) + "\"}");
            return false;
        }
    }

    static void KillChild()
    {
        if (child != null)
        {
            try { if (!child.HasExited) child.Kill(); } catch { }
            try { child.Dispose(); } catch { }
            child = null;
        }
    }

    static string JsonEsc(string s)
    {
        if (s == null) return "";
        return s.Replace("\\", "\\\\").Replace("\"", "\\\"").Replace("\r", "\\r").Replace("\n", "\\n");
    }

    // 后台线程排空子进程输出，防止管道写满阻塞子进程，也防止日志混入消息管道
    static void Drain(System.IO.TextReader reader)
    {
        System.Threading.Thread t = new System.Threading.Thread(() =>
        {
            try { while (reader.ReadLine() != null) { } } catch { }
        });
        t.IsBackground = true;
        t.Start();
    }

    static int Main()
    {
        Stream inp = Console.OpenStandardInput();
        byte[] head = new byte[4];
        while (true)
        {
            int got = ReadExact(inp, head, 4);
            if (got < 4) break; // EOF / 关闭
            uint len = BitConverter.ToUInt32(head, 0);
            if (len == 0 || len > 1 << 20) break; // 防呆
            byte[] body = new byte[len];
            if (ReadExact(inp, body, body.Length) < body.Length) break;
            string msg;
            try { msg = Encoding.UTF8.GetString(body); } catch { msg = ""; }
            try
            {
                if (msg.Contains("\"start\""))
                {
                    bool ok = SpawnServer();
                    if (ok) WriteReply("{\"ok\":true,\"already\":" + (child != null ? "true" : "false") + "}");
                }
                else if (msg.Contains("\"stop\""))
                {
                    KillChild();
                    WriteReply("{\"ok\":true}");
                }
                else if (msg.Contains("\"ping\""))
                {
                    bool running = child != null && !child.HasExited;
                    WriteReply("{\"ok\":true,\"running\":" + (running ? "true" : "false") + "}");
                }
                else
                {
                    WriteReply("{\"ok\":false,\"error\":\"unknown message\"}");
                }
            }
            catch (Exception e)
            {
                WriteReply("{\"ok\":false,\"error\":\"" + JsonEsc(e.Message) + "\"}");
            }
        }
        KillChild(); // 宿主退出（浏览器/SW 关闭）-> 停服务
        return 0;
    }
}
