// 强testbench：参考模型跟着DUT走，每拍核对空满标志和读出的数据；
// 覆盖写满后再写、读空后再读、同时读写和随机读写
module tb_full;
  localparam DEPTH = 8;
  reg        clk = 0, rst = 1, wr_en = 0, rd_en = 0;
  reg  [7:0] wr_data = 0;
  wire [7:0] rd_data;
  wire       full, empty;

  reg  [7:0] model [0:DEPTH-1];   // 参考模型：环形缓冲
  integer    head = 0, tail = 0, count = 0, prev, errors = 0, cyc;
  reg  [7:0] expect_rd;
  reg        wr_ok, rd_ok, read_happened = 0;

  sync_fifo dut (.clk(clk), .rst(rst), .wr_en(wr_en), .wr_data(wr_data),
                 .rd_en(rd_en), .rd_data(rd_data), .full(full), .empty(empty));

  always #5 clk = ~clk;

  // 时钟沿：按沿之前的状态推进模型（满了不写、空了不读）
  always @(posedge clk) begin
    if (!rst) begin
      prev   = count;
      wr_ok  = wr_en && (prev < DEPTH);
      rd_ok  = rd_en && (prev > 0);
      if (rd_ok) begin
        expect_rd = model[head];
        head      = (head + 1) % DEPTH;
      end
      if (wr_ok) begin
        model[tail] = wr_data;
        tail        = (tail + 1) % DEPTH;
      end
      count         = prev + wr_ok - rd_ok;
      read_happened = rd_ok;
    end
  end

  // 下降沿：核对空满标志和读出的数据
  always @(negedge clk) begin
    if (!rst) begin
      if (empty !== (count == 0)) begin
        errors = errors + 1;
        $display("ERROR @%0t: empty=%b, model count=%0d", $time, empty, count);
      end
      if (full !== (count == DEPTH)) begin
        errors = errors + 1;
        $display("ERROR @%0t: full=%b, model count=%0d", $time, full, count);
      end
      if (read_happened && rd_data !== expect_rd) begin
        errors = errors + 1;
        $display("ERROR @%0t: rd_data=%h, expected %h", $time, rd_data, expect_rd);
      end
    end
  end

  initial begin
    repeat (2) @(posedge clk);
    rst <= 0;
    repeat (DEPTH + 2) begin            // 写满，再多写两次：满了以后的写要被忽略
      @(posedge clk);
      wr_en <= 1; rd_en <= 0; wr_data <= $random;
    end
    repeat (DEPTH + 2) begin            // 读空，再多读两次：空了以后的读不能动指针
      @(posedge clk);
      wr_en <= 0; rd_en <= 1;
    end
    for (cyc = 0; cyc < 300; cyc = cyc + 1) begin   // 随机读写，包括同时读写
      @(posedge clk);
      wr_en <= $random; rd_en <= $random; wr_data <= $random;
    end
    @(posedge clk);
    wr_en <= 0; rd_en <= 0;
    @(posedge clk);
    if (errors == 0) $display("TEST PASSED");
    else             $display("TEST FAILED: %0d errors", errors);
    $finish;
  end
endmodule
