"""分析工具：analyze_csv, search_papers, parse_slx_model, calculate_pi_params,
   generate_svpwm_table, read_matlab_script, suggest_controller_params"""

import csv
import json
import math
import os
import re
import subprocess
from pathlib import Path

import chardet

from config import PROJECT_ROOT, DESKTOP
from tools._common import _resolve_path

def _analyze_csv(args: dict) -> str:
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        sample = path.read_bytes()[:65536]
        encoding = chardet.detect(sample)["encoding"] or "utf-8"

        target_cols = args.get("columns", "")
        target_names = [c.strip() for c in target_cols.split(",") if c.strip()]

        with path.open("r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames or []
            if not fieldnames:
                return "错误: CSV 文件无法解析表头"

            probe_rows = []
            total_rows = 0
            for row in reader:
                total_rows += 1
                if len(probe_rows) < 50:
                    probe_rows.append(row)

        if target_names:
            numeric_columns = [c for c in target_names if c in fieldnames]
        else:
            numeric_columns = []
            for col in fieldnames:
                numeric_hits = 0
                for row in probe_rows:
                    try:
                        float(row[col])
                        numeric_hits += 1
                    except (ValueError, KeyError, TypeError):
                        pass
                if numeric_hits > 0:
                    numeric_columns.append(col)

        if not numeric_columns:
            return f"错误: 未找到数值列。可用列: {', '.join(fieldnames)}"

        steady_start = int(total_rows * 0.7)
        stats = {
            col: {
                "n": 0, "sum": 0.0, "min": None, "max": None,
                "steady_n": 0, "steady_sum": 0.0, "steady_sum_sq": 0.0,
            }
            for col in numeric_columns
        }

        with path.open("r", encoding=encoding, errors="replace", newline="") as f:
            reader = csv.DictReader(f)
            for row_idx, row in enumerate(reader):
                for col in numeric_columns:
                    try:
                        val = float(row[col])
                    except (ValueError, KeyError, TypeError):
                        continue

                    st = stats[col]
                    st["n"] += 1
                    st["sum"] += val
                    st["min"] = val if st["min"] is None else min(st["min"], val)
                    st["max"] = val if st["max"] is None else max(st["max"], val)

                    if row_idx >= steady_start:
                        st["steady_n"] += 1
                        st["steady_sum"] += val
                        st["steady_sum_sq"] += val * val

        output_lines = [f"CSV 分析: {path.name}", f"共 {total_rows} 行, 数值列: {numeric_columns}", "=" * 50]

        for col in numeric_columns:
            st = stats[col]
            if st["n"] == 0:
                continue

            mean_val = st["sum"] / st["n"]
            steady_n = max(st["steady_n"], 1)
            steady_mean = st["steady_sum"] / steady_n
            variance = max(st["steady_sum_sq"] / steady_n - steady_mean * steady_mean, 0.0)
            ripple = variance ** 0.5

            output_lines.append(
                f"\n{col}:\n"
                f"  范围: [{st['min']:.4f}, {st['max']:.4f}]\n"
                f"  均值: {mean_val:.4f}\n"
                f"  稳态均值(后30%): {steady_mean:.4f}\n"
                f"  稳态纹波: {ripple:.4f} ({ripple / max(abs(steady_mean), 0.001) * 100:.2f}%)"
            )

        return "\n".join(output_lines)

    except Exception as e:
        return f"CSV 分析失败: {e}"


def _search_papers(args: dict) -> str:
    keyword = args["keyword"].lower()
    results = []

    try:
        for item in DESKTOP.iterdir():
            if item.is_file() and item.suffix.lower() == ".pdf":
                name = item.name.lower()
                if keyword in name:
                    results.append(item.name)

        if not results:
            return f"未在桌面找到包含 '{keyword}' 的论文文件"
        return "找到以下相关论文:\n" + "\n".join(f"  - {r}" for r in sorted(results))
    except Exception as e:
        return f"搜索论文失败: {e}"


def _parse_slx_model(args: dict) -> str:
    """解析 Simulink .slx 模型结构"""
    import zipfile

    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        with zipfile.ZipFile(path, "r") as zf:
            names = zf.namelist()

            # 找到 XML 主文件
            xml_file = None
            for n in names:
                if n.endswith(".xml") and not n.startswith("_"):
                    xml_file = n
                    break

            if not xml_file:
                return f"SLX 解析: 共 {len(names)} 个内部文件，但未找到主 XML\n文件列表:\n" + "\n".join(f"  {n}" for n in sorted(names)[:30])

            xml_content = zf.read(xml_file).decode("utf-8", errors="ignore")

            # 提取 Block 名称
            blocks = re.findall(r'BlockType="([^"]+)"', xml_content)
            block_names = re.findall(r'Name="([^"]+)"', xml_content)

            block_counts = {}
            for b in blocks:
                block_counts[b] = block_counts.get(b, 0) + 1

            return (
                f"SLX 模型分析: {path.name}\n"
                f"  内部文件数: {len(names)}\n"
                f"  主 XML: {xml_file}\n"
                f"  模块总数: {len(blocks)}\n\n"
                f"模块类型统计:\n" +
                "\n".join(f"  {bt}: {cnt}" for bt, cnt in sorted(block_counts.items(), key=lambda x: -x[1])[:25]) +
                f"\n\n子系统/模块名称:\n" +
                "\n".join(f"  {n}" for n in block_names[:40] if len(n) > 2)
            )
    except Exception as e:
        return f"SLX 解析失败: {e}"


def _calculate_pi_params(args: dict) -> str:
    """根据电机参数计算 PI 控制器增益（带宽法）"""
    Rs = float(args["Rs"])
    Ld = float(args["Ld"])
    Lq = float(args["Lq"])
    flux = float(args["flux"])
    poles = int(args["poles"])
    J = float(args.get("J", 0))
    Ts_c = float(args.get("Ts_current", 1e-4))
    Ts_s = float(args.get("Ts_speed", 1e-3))
    bw_c = float(args.get("bandwidth_current", 500))   # Hz
    bw_s = float(args.get("bandwidth_speed", 50))        # Hz

    import math

    # 电流环 PI（零极点对消法）
    wc = 2 * math.pi * bw_c
    Kp_d = wc * Ld
    Ki_d = wc * Rs
    Kp_q = wc * Lq
    Ki_q = wc * Rs

    # 速度环 PI（对称最优法）
    Kt = 1.5 * poles * flux       # 转矩常数
    ws = 2 * math.pi * bw_s
    if J > 0:
        Kp_s = ws * J / Kt
        Ki_s = Kp_s * ws / 4       # 对称最优：Ti = 4/wc
    else:
        Kp_s, Ki_s = 0, 0

    # 数字实现时的积分限幅建议
    i_limit = 1.2 * 10  # 假设额定电流约 10A

    return (
        f"PI 参数计算结果 (带宽法)\n"
        f"{'='*50}\n"
        f"电机参数: Rs={Rs} Ohm, Ld={Ld} H, Lq={Lq} H, Flux={flux} Wb, Poles={poles}\n"
        f"{'='*50}\n\n"
        f"【电流环】 (fc = {bw_c} Hz, Ts = {Ts_c*1e6:.0f} us)\n"
        f"  Kp_d = {Kp_d:.4f}   Ki_d = {Ki_d:.4f}\n"
        f"  Kp_q = {Kp_q:.4f}   Ki_q = {Ki_q:.4f}\n"
        f"  零极点对消频率: {wc:.1f} rad/s\n"
        f"  积分限幅建议: +/- {i_limit:.1f}\n"
        f"  离散化: 后向欧拉, Ki_disc = Ki * Ts_c\n\n"
        f"【速度环】 (fs = {bw_s} Hz, Ts = {Ts_s*1e3:.1f} ms)\n"
        f"  Kp_s = {Kp_s:.6f}   Ki_s = {Ki_s:.6f}\n"
        f"  转矩常数 Kt = {Kt:.4f} Nm/A\n" +
        (f"  惯量 J = {J:.6f} kg.m^2\n" if J > 0 else "  (未提供惯量 J，无法完整计算速度环)\n") +
        f"\n【设计检查】\n"
        f"  带宽比 fc/fs = {bw_c/bw_s:.1f} (建议 > 10 以避免环间干扰)\n"
        f"  数字延迟裕度: {1/Ts_c/bw_c:.1f}x 采样频率\n"
    )


def _generate_svpwm_table(args: dict) -> str:
    """生成 SVPWM 扇区切换表"""
    phases = int(args.get("phases", 3))
    Ts = float(args.get("Ts", 1e-4))
    Vdc = float(args.get("Vdc", 300))

    if phases == 3:
        import math
        # 三相 SVPWM 基本矢量
        v_mag = Vdc * 2.0 / 3.0
        sectors = []
        for k in range(1, 7):
            theta_k = (k - 1) * math.pi / 3
            sectors.append(f"  扇区 {k}: 角度 [{math.degrees(theta_k):.0f}, {math.degrees(theta_k + math.pi/3):.0f}) deg")

        return (
            f"SVPWM 扇区表 ({phases}相)\n"
            f"{'='*50}\n"
            f"直流母线电压: {Vdc} V\n"
            f"PWM 周期: {Ts*1e6:.0f} us\n"
            f"基本矢量幅值: {v_mag:.1f} V\n"
            f"最大调制比 (线性): {Vdc/math.sqrt(3):.1f} V (相电压峰值)\n\n"
            f"【扇区判定 - 6 个扇区】\n" +
            "\n".join(sectors) +
            f"\n\n【矢量作用时间】 (Ts={Ts*1e6:.0f}us)\n"
            f"  T1 = sqrt(3)*|Uref|*Ts*sin(pi/3 - theta) / Vdc\n"
            f"  T2 = sqrt(3)*|Uref|*Ts*sin(theta) / Vdc\n"
            f"  T0 = Ts - T1 - T2\n\n"
            f"【各扇区开关序列】\n"
            f"  扇区1: 000-100-110-111-110-100-000\n"
            f"  扇区2: 000-110-010-111-010-110-000\n"
            f"  扇区3: 000-010-011-111-011-010-000\n"
            f"  扇区4: 000-011-001-111-001-011-000\n"
            f"  扇区5: 000-001-101-111-101-001-000\n"
            f"  扇区6: 000-101-100-111-100-101-000\n\n"
            f"【C2000 实现提示】\n"
            f"  - 使用 EPwm 模块的 TBPRD 设定周期\n"
            f"  - CMPA/CMPB 更新在 CTR=0 或 PRD 时触发\n"
            f"  - 七段式 SVPWM 在每个周期内采样两次，减少谐波\n"
        )
    else:
        return (
            f"双三相 SVPWM ({phases}相)\n"
            f"{'='*50}\n"
            f"【双三相解耦 SVPWM】\n"
            f"  将六相分解为两个三相子系统 (ABC + XYZ)\n"
            f"  各自独立进行三相 SVPWM\n"
            f"  载波相位差 30° 以减少 5/7 次谐波\n\n"
            f"【VSD 变换矩阵】\n"
            f"  alpha-beta 平面: 转矩产生分量\n"
            f"  xy 平面: 谐波分量（被 ESO 抑制）\n"
            f"  o1-o2 平面: 零序分量\n"
        )


def _read_matlab_script(args: dict) -> str:
    """读取 MATLAB .m 脚本并结构化展示"""
    path_str = args.get("path", "")
    if not path_str:
        return "错误: 缺少 path 参数"
    path = _resolve_path(path_str)
    if not path.exists():
        return f"错误: 文件不存在: {path}"

    try:
        content = path.read_text(encoding="utf-8", errors="ignore")

        # 提取变量赋值
        assignments = re.findall(r'^(\w+)\s*=\s*(.+?);?\s*$', content, re.MULTILINE)

        # 提取函数定义
        functions = re.findall(r'^function\s+(.+?)$', content, re.MULTILINE)

        # 提取 Simulink 调用
        sim_calls = re.findall(r"(sim|load_system|open_system)\(([^)]+)\)", content)

        # 提取注释行（以 % 开头）
        comments = [l.strip()[1:].strip() for l in content.split("\n") if l.strip().startswith("%")]

        output = [
            f"MATLAB 脚本分析: {path.name}",
            f"总行数: {len(content.splitlines())}",
            f"",
        ]

        if functions:
            output.append(f"[函数定义] ({len(functions)}个):")
            output.extend(f"  - {f}" for f in functions)
            output.append("")

        if assignments:
            output.append(f"[变量赋值] ({len(assignments)}个, 仅显示前20):")
            for var, val in assignments[:20]:
                output.append(f"  {var} = {val[:120]}")
            output.append("")

        if sim_calls:
            output.append(f"[Simulink 调用] ({len(sim_calls)}个):")
            for cmd, arg in sim_calls:
                output.append(f"  {cmd}({arg})")
            output.append("")

        if comments:
            output.append(f"[注释摘要]:")
            output.extend(f"  % {c[:150]}" for c in comments[:10])

        return "\n".join(output)
    except Exception as e:
        return f"MATLAB 脚本读取失败: {e}"


def _suggest_controller_params(args: dict) -> str:
    """根据电机类型和建议控制策略"""
    motor = args["motor_type"].upper()
    speed = float(args["rated_speed"])
    current = float(args.get("rated_current", 10))
    voltage = float(args.get("rated_voltage", 310))
    target = args.get("control_target", "speed")

    suggestions = []

    if motor == "PMSM":
        suggestions = [
            ("电流环", "PI (零极点对消)", "Kp = wc*L, Ki = wc*R", "带宽 300-800 Hz"),
            ("速度环", "PI (对称最优)", "Kp = w*J/Kt, Ki = Kp*w/4", "带宽 30-80 Hz"),
            ("电流环(增强)", "SMC (滑模)", "切换增益 > 扰动上界", "适合参数摄动大"),
            ("扰动补偿", "ESO (扩展状态观测器)", "L1=1000-2000, L2=10^5-10^6", "需整定带宽比 1:10"),
            ("速度环(增强)", "ADRC (自抗扰)", "wc=50-200, wo=3-5*wc, b0=Kt/J", "鲁棒性强"),
            ("MTPA", "查表/公式", f"Id = (flux/(Lq-Ld))*(1-sqrt(1+...))", "IPM 专用"),
            ("弱磁", "电压闭环", f"Udc > {voltage*0.577:.0f}V 时生效", "高速区"),
        ]
    elif motor == "IM":
        suggestions = [
            ("电流环", "PI (零极点对消)", "Kp = sigma*Ls*wc, Ki = Rs*wc", "带宽 300-500 Hz"),
            ("速度环", "PI (对称最优)", "Kp = w*J/Kt, Ki = Kp*w/4", "带宽 20-50 Hz"),
            ("磁链观测", "全阶观测器", "极点位于电机极点左侧 2-3 倍", "或 Gopinath 模型"),
        ]
    elif motor == "BLDC":
        suggestions = [
            ("电流环", "PI", "Kp=0.5*Vdc/Imax, Ki=Kp*100", "梯形波控制"),
            ("速度环", "PI", "Kp=0.01*Vdc/Ke, Ki=Kp*50", "Hall 传感器反馈"),
        ]

    return (
        f"控制策略建议: {motor} @ {speed:.0f} RPM\n"
        f"{'='*50}\n" +
        "\n".join(
            f"【{s[0]}】{s[1]}\n"
            f"  参数: {s[2]}\n"
            f"  备注: {s[3]}\n"
            for s in suggestions
        )
    )
