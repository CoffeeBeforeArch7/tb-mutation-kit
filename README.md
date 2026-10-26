# tb-mutation-kit

用变异体检查testbench能不能抓bug：在被测RTL里每次只改一处（`==`改`!=`、`&&`改`||`、去掉一个`!`、`+`改`-`、if条件取反、`posedge`改`negedge`……），逐个跑你自己的仿真命令，报告哪些bug testbench没发现。

覆盖率说明testbench跑到了哪里，变异体说明跑到了以后能不能发现错。

- `skill/mutate.py`：造变异体、跑仿真、出报告，只用Python标准库
- `skill/SKILL.md`：给Claude Code用的skill，说明怎么用脚本、怎么分析没抓到的变异体
- `examples/sync_fifo/`：演示用的同步FIFO和两个testbench

## 演示：同步FIFO

`examples/sync_fifo/`里有一个8深的同步FIFO，脚本在它上面造了14个变异体：

| testbench | 做了什么 | 分数 | 没抓到 |
| --- | --- | --- | --- |
| `tb_basic.sv` | 写3个数再读出来，只核对顺序 | 86%（12/14） | full的低位比较写反；posedge改negedge |
| `tb_full.sv` | 参考模型每拍核对空满标志和数据，覆盖写满、读空、同时读写和随机读写 | 93%（13/14） | posedge改negedge |

- `tb_basic`没抓到full的错，因为它从来没把FIFO写满
- 两个testbench都没抓到posedge改negedge：它们都在下降沿核对输出，DUT换成下降沿触发，核对时看到的值恰好一样。像UVM monitor那样在上升沿采样就能抓到

自己跑一遍（需要[Icarus Verilog](https://github.com/steveicarus/iverilog)）：

```bash
cd examples/sync_fifo
python3 ../../skill/mutate.py --rtl sync_fifo.sv \
  --cmd "iverilog -g2012 -o sim sync_fifo.sv tb_full.sv && vvp -n sim" \
  --pass-regex "TEST PASSED" --timeout 30
```

报告写在`mutation_report/report.md`。

## 装成Claude Code的skill

```bash
ln -s "$PWD/skill" ~/.claude/skills/tb-mutation
```

之后在Claude Code里说「检查一下这个testbench能不能抓bug」，或者直接用`/tb-mutation`。它会先问清楚被测RTL、仿真命令和通过判据，跑完再把没抓到的变异体分成testbench的漏洞、采样时刻的盲区和等价变异体三类，给出改进建议。

## 变异算子

| 算子 | 改法 | 在哪改 |
| --- | --- | --- |
| EQ_NE、NE_EQ | `==` ↔ `!=` | 表达式 |
| AND_OR、OR_AND | `&&` ↔ `\|\|` | 表达式 |
| NOT_DEL | 去掉逻辑非`!` | 表达式 |
| LT_LE、LE_LT、GT_GE、GE_GT | `<` ↔ `<=`，`>` ↔ `>=` | if/while条件和assign右边（别处的`<=`是非阻塞赋值） |
| PLUS_MINUS、MINUS_PLUS | `+` ↔ `-` | 表达式 |
| CONST_INC | 常数加一 | if/while条件和assign右边 |
| INDEX | 常数下标加一或减一 | 表达式 |
| COND_NEG | if/while条件取反 | 条件 |
| EDGE | `posedge` ↔ `negedge` | 敏感列表 |

不改的地方：注释和字符串、parameter行、声明的等号左边、for循环头、`[a:b]`位宽范围。

## 结果怎么算

- **killed**：testbench报错（退出码非0、没出现通过关键字，或出现了失败关键字）
- **survived**：testbench照样通过，要重点看的就是这些
- **timeout**：超时，算抓到
- **stillborn**：编译不过，不算分
- **分数** = (killed + timeout) / (killed + survived + timeout)

## 参考

- [PRO-V-R1: Reasoning Enhanced Programming Agent for RTL Verification](https://arxiv.org/abs/2506.12200)：用变异体衡量AI写的testbench能不能抓bug
- [从评测到agent：NVIDIA用大模型做RTL设计与验证的14篇论文](https://coffeebeforearch.com/blog/nvidia-ai-for-chip-design-papers/)：这个工具的来由
