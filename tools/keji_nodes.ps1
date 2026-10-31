# 科技树图片 → 文字标签的坐标（开发工具，不参与游戏运行）。
#
# 用途：png/keji.png 与 png/keji2.png 里每个科技名就是一行文字，没有任何连线。
# 新增科技时，用本工具读出该名字在图片里的中心坐标，写进 world/techs.json 的 (x, y)。
# 坐标零点＝图片左上角，x 向右，y 向下。
#
# 用法：
#   powershell -File tools\keji_nodes.ps1                       # 整张 keji.png
#   powershell -File tools\keji_nodes.ps1 -X 4900 -Y 1300 -W 800 -H 320
#   powershell -File tools\keji_nodes.ps1 -Image png\keji2.png
# 输出：每行一个标签框 —— 中心x,中心y,宽,高。同一个标签里的几个字并成一个（字距约 3px），
#       相邻标签一般隔 17px 以上，所以 -Gap 默认 8：只并字，不并邻居。
param(
    [string]$Image = "png\keji.png",
    [int]$X = 0,
    [int]$Y = 0,
    [int]$W = 0,
    [int]$H = 0,
    [int]$Threshold = 160,
    [int]$Gap = 8
)

Add-Type -AssemblyName System.Drawing
Add-Type -TypeDefinition @"
using System;
using System.Drawing;
using System.Drawing.Imaging;
using System.Collections.Generic;
using System.Runtime.InteropServices;

public class KejiBoxes {
    public static List<int[]> Labels(string path, int rx, int ry, int rw, int rh, int thr, int maxGap) {
        using (var bmp = new Bitmap(path)) {
            int w = bmp.Width, h = bmp.Height;
            if (rw <= 0) { rx = 0; ry = 0; rw = w; rh = h; }
            if (rx + rw > w) rw = w - rx;
            if (ry + rh > h) rh = h - ry;
            var data = bmp.LockBits(new Rectangle(0, 0, w, h), ImageLockMode.ReadOnly, PixelFormat.Format32bppArgb);
            int stride = data.Stride;
            byte[] buf = new byte[stride * h];
            Marshal.Copy(data.Scan0, buf, 0, buf.Length);
            bmp.UnlockBits(data);

            bool[] dark = new bool[rw * rh];
            for (int y = 0; y < rh; y++) {
                int row = (y + ry) * stride;
                for (int x = 0; x < rw; x++) {
                    int i = row + (x + rx) * 4;
                    if (buf[i] < thr && buf[i + 1] < thr && buf[i + 2] < thr) dark[y * rw + x] = true;
                }
            }

            var comps = new List<int[]>();
            bool[] seen = new bool[rw * rh];
            var stack = new Stack<int>();
            for (int i = 0; i < rw * rh; i++) {
                if (!dark[i] || seen[i]) continue;
                stack.Clear(); stack.Push(i); seen[i] = true;
                int minx = i % rw, maxx = minx, miny = i / rw, maxy = miny, cnt = 0;
                while (stack.Count > 0) {
                    int p = stack.Pop(); int px = p % rw, py = p / rw; cnt++;
                    if (px < minx) minx = px; if (px > maxx) maxx = px;
                    if (py < miny) miny = py; if (py > maxy) maxy = py;
                    for (int dy = -1; dy <= 1; dy++) for (int dx = -1; dx <= 1; dx++) {
                        int nx = px + dx, ny = py + dy;
                        if (nx < 0 || ny < 0 || nx >= rw || ny >= rh) continue;
                        int q = ny * rw + nx;
                        if (dark[q] && !seen[q]) { seen[q] = true; stack.Push(q); }
                    }
                }
                if (cnt >= 8) comps.Add(new int[] { minx, miny, maxx - minx + 1, maxy - miny + 1 });
            }

            // 同一行相邻的字并成一个标签
            bool merged = true;
            while (merged) {
                merged = false;
                for (int i = 0; i < comps.Count && !merged; i++) {
                    for (int j = i + 1; j < comps.Count && !merged; j++) {
                        int[] a = comps[i], b = comps[j];
                        int top = Math.Max(a[1], b[1]), bot = Math.Min(a[1] + a[3], b[1] + b[3]);
                        double vov = Math.Max(0, bot - top) / (double)Math.Min(a[3], b[3]);
                        int gap = Math.Max(a[0], b[0]) - Math.Min(a[0] + a[2], b[0] + b[2]);
                        if (vov > 0.45 && gap <= maxGap) {
                            int x0 = Math.Min(a[0], b[0]), y0 = Math.Min(a[1], b[1]);
                            int x1 = Math.Max(a[0] + a[2], b[0] + b[2]), y1 = Math.Max(a[1] + a[3], b[1] + b[3]);
                            comps[i] = new int[] { x0, y0, x1 - x0, y1 - y0 };
                            comps.RemoveAt(j);
                            merged = true;
                        }
                    }
                }
            }
            for (int k = 0; k < comps.Count; k++) {
                comps[k][0] += rx; comps[k][1] += ry;
            }
            comps.Sort(delegate (int[] a, int[] b) {
                int c = a[1].CompareTo(b[1]);
                return c != 0 ? c : a[0].CompareTo(b[0]);
            });
            return comps;
        }
    }
}
"@ -ReferencedAssemblies System.Drawing

$boxes = [KejiBoxes]::Labels((Resolve-Path $Image).Path, $X, $Y, $W, $H, $Threshold, $Gap)
"# $Image 标签数 = $($boxes.Count)"
foreach ($b in $boxes) {
    $cx = [int]($b[0] + $b[2] / 2)
    $cy = [int]($b[1] + $b[3] / 2)
    "$cx,$cy,$($b[2]),$($b[3])"
}
