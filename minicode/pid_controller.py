"""minicode 的 PID 控制器（Day 28）。控制论基础。

PID 公式：error = setpoint - measurement
         output = Kp*error + Ki*∫error + Kd*d(error)/dt

三个关键工程点（课件「工程重点是采样周期、输出限幅、anti-windup 和 actuator 是否真的执行」）：
1. 输出限幅：output clamp 到 [output_min, output_max]；
2. anti-windup（条件积分）：只在「输出未饱和」时才接受积分累加——否则执行器饱和期间，
   积分项会持续累积，导致解除饱和后输出回不来（windup）；
3. reset：清空积分和上次误差，供「禁用/重启控制器」用。

注意 anti-windup 有两种常见实现，这里用「条件积分」（conditional integration），区别于
「积分限幅」（integral clamping，把积分项 clamp 到一个固定上下限）。条件积分更贴合
「执行器饱和」的语义——输出没饱和才累积，饱和了就冻结积分。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(slots=True)
class PID:
    kp: float
    ki: float
    kd: float
    output_min: float = -1.0
    output_max: float = 1.0
    integral: float = 0.0
    previous_error: float = 0.0

    def update(self, setpoint: float, measurement: float, dt: float = 1.0) -> float:
        """算一次控制输出，返回限幅后的 output。dt 是采样周期。"""
        error = setpoint - measurement
        candidate_integral = self.integral + error * dt
        derivative = (error - self.previous_error) / max(dt, 1e-9)
        raw = self.kp * error + self.ki * candidate_integral + self.kd * derivative
        output = min(self.output_max, max(self.output_min, raw))
        # anti-windup：只在未饱和（output == raw）时接受积分累加
        if output == raw:
            self.integral = candidate_integral
        self.previous_error = error
        return output

    def reset(self) -> None:
        self.integral = 0.0
        self.previous_error = 0.0
