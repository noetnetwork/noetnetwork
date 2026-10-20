#include "server-task.h"
#include <iostream>
#include <stdexcept>

static void require(bool condition, const char * message) {
    if (!condition) throw std::runtime_error(message);
}

int main() {
    try {
        unsigned cases = 0;
        for (auto type : {TASK_RESPONSE_TYPE_NONE, TASK_RESPONSE_TYPE_OAI_CMPL}) {
            task_result_state state{common_chat_parser_params{}};
            server_task_result_cmpl_partial partial{};
            partial.res_type = type;
            partial.content = "A numerical example: ";
            partial.n_decoded = 7;
            partial.update(state);
            require(state.generated_text.empty(), "Text stream entered the chat parser");
            auto chunk = partial.to_json();
            require((type == TASK_RESPONSE_TYPE_NONE ? chunk.at("content") : chunk.at("choices").at(0).at("text")) == partial.content,
                    "Text stream changed content");
            ++cases;

            server_task_result_cmpl_final final{};
            final.res_type = type;
            final.content = "2 + 2 = 4.";
            final.n_decoded = 516;
            final.n_prompt_tokens = 36;
            final.update(state);
            require(state.generated_text.empty(), "Text completion entered the chat parser");
            auto completed = final.to_json();
            require((type == TASK_RESPONSE_TYPE_NONE ? completed.at("content") : completed.at("choices").at(0).at("text")) == final.content,
                    "Text completion changed content");
            require((type == TASK_RESPONSE_TYPE_NONE ? completed.at("tokens_predicted") : completed.at("usage").at("completion_tokens")) == 516,
                    "Text completion changed token accounting");
            ++cases;

            // Completion can stop in the middle of a multibyte token. Response
            // updating must not require a complete chat message in that case.
            final.content = std::string("A value: ") + char(0xe2) + char(0x88);
            final.update(state);
            require(state.generated_text.empty(), "Incomplete text entered the chat parser");
            ++cases;
        }
        for (auto type : {TASK_RESPONSE_TYPE_OAI_CHAT, TASK_RESPONSE_TYPE_OAI_RESP, TASK_RESPONSE_TYPE_ANTHROPIC}) {
            task_result_state state{common_chat_parser_params{}};
            server_task_result_cmpl_partial partial{};
            partial.res_type = type;
            partial.content = "Hello";
            partial.update(state);
            require(state.generated_text == "Hello", "Chat stream stopped parsing");
            server_task_result_cmpl_final final{};
            final.res_type = type;
            final.content = " world";
            final.update(state);
            require(state.generated_text == "Hello world", "Chat completion stopped parsing");
            ++cases;
        }
        std::cout << "{\"passed\":true,\"cases\":" << cases << "}\n";
    } catch (const std::exception & error) {
        std::cerr << error.what() << '\n';
        return 1;
    }
}
