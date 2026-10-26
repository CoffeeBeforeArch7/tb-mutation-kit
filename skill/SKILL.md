---
name: tb-mutation
description: "Mutation testing for RTL testbenches: inject one small bug at a time into the DUT (flip ==/!=, &&/||, drop a !, +/-, negate an if condition, swap posedge/negedge...), re-run the user's own simulation command for each mutant, report which bugs the testbench missed, then analyze the survivors and suggest the missing stimulus or checks. Use when the user asks how strong a Verilog/SystemVerilog/UVM testbench is, whether it would catch bugs, or wants a mutation score; triggers on 变异测试 / 变异体 / testbench能不能抓bug / testbench检错能力 / 检查testbench质量. Also user-invocable via /tb-mutation."
user-invocable: true
---

# tb-mutation：用变异体检查testbench能不能抓bug

覆盖率说明testbench跑到了哪里，变异体说明跑到了以后能不能发现错：在正确的RTL里每次只改一处，看testbench会不会报错。这是软件测试里的mutation testing；PRO-V-R1这类论文也用变异体评估AI写的testbench。

脚本是同一目录下的`mutate.py`，只用Python标准库，仿真用用户自己的命令。

## 一、先弄清楚四件事

缺了就问用户，不要猜：
1. **被测RTL文件**：只变异DUT，不变异testbench、VIP、参考模型
2. **仿真命令**：在项目根目录能直接跑的一条命令，如`make sim`、`iverilog ... && vvp ...`、`./run.sh test_x`
3. **怎么判通过**：退出码，加上日志里的通过关键字（`--pass-regex`）或失败关键字（`--fail-regex`）。UVM常用`--fail-regex "UVM_(ERROR|FATAL)\s*:\s*[1-9]"`
4. **在哪台机器跑**：仿真器在哪台机器，就把`mutate.py`复制过去在那里跑

## 二、跑

1. 先只列出变异体，确认改的位置合理：
   ```bash
   python3 <本目录>/mutate.py --project <项目根> --rtl <rtl文件> --cmd "<仿真命令>" --list
   ```
2. 正式跑。原始设计必须先通过，否则脚本会停下：
   ```bash
   python3 <本目录>/mutate.py --project <项目根> --rtl <rtl文件> [--rtl ...] \
     --cmd "<仿真命令>" --pass-regex "<通过关键字>" --timeout <单次秒数> --max 40 --out mutation_report
   ```
   - 编译和运行能分开时，用`--build "<编译命令>"`：编译不过的变异体直接判stillborn
   - 仿真慢就加`-j N`并行，每份一个项目副本，注意license够不够
   - 项目里有大的编译产物目录，用`--exclude <目录名>`跳过
3. 结果在`--out`目录：`report.md`给人看，`report.json`给你分析

## 三、分析没抓到的变异体

逐个看survived，分三类写清楚：
- **testbench的漏洞**：这个bug在真实使用中会出问题，testbench没测到。写出缺的是哪种激励（如「从没写满过」）或哪种检查（如「从不核对full」），以及加什么能抓到
- **采样时刻的盲区**：bug存在，但testbench核对输出的时刻看不出来（如只在下降沿核对，DUT换成下降沿触发看到的值一样）。说明在哪个时刻采样能抓到
- **等价变异体**：改完行为完全不变，如改的分支永远走不到、改的信号没人用。说清楚为什么等价，不算testbench的问题

最后汇报：分数，三类各几个，按影响从大到小排的改进建议。

## 四、规矩

- **不自动改用户的testbench**：只给建议，用户同意后再改
- **不碰原项目**：脚本在临时副本里改RTL，跑完删掉；加`--keep`才保留副本
- **怎么算分**：killed是testbench报错；timeout算抓到（testbench发现了异常，比如卡死）；stillborn是编译不过，不算分。分数 = (killed + timeout) / (killed + survived + timeout)
- **自带的算子只做逐处小改**：想测状态机转移错、握手时序违例这类更真实的bug，手工写几个改过的RTL，用同一条命令单独跑
