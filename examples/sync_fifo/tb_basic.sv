// 弱testbench：写3个数再读出来，只核对顺序；不查空满，也不在满时写、空时读
module tb_basic;
  reg        clk = 0, rst = 1, wr_en = 0, rd_en = 0;
  reg  [7:0] wr_data = 0;
  wire [7:0] rd_data;
  wire       full, empty;
  integer    i, errors = 0;

  sync_fifo dut (.clk(clk), .rst(rst), .wr_en(wr_en), .wr_data(wr_data),
                 .rd_en(rd_en), .rd_data(rd_data), .full(full), .empty(empty));

  always #5 clk = ~clk;

  initial begin
    repeat (2) @(posedge clk);
    rst <= 0;
    for (i = 0; i < 3; i = i + 1) begin
      @(posedge clk);
      wr_en   <= 1;
      wr_data <= 8'h10 + i;
    end
    @(posedge clk);
    wr_en <= 0;
    for (i = 0; i < 3; i = i + 1) begin
      @(posedge clk);
      rd_en <= 1;
      @(posedge clk);
      rd_en <= 0;
      @(negedge clk);
      if (rd_data !== 8'h10 + i) begin
        errors = errors + 1;
        $display("ERROR: read %h, expected %h", rd_data, 8'h10 + i);
      end
    end
    if (errors == 0) $display("TEST PASSED");
    else             $display("TEST FAILED: %0d errors", errors);
    $finish;
  end
endmodule
